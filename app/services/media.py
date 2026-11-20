"""Bounded media analysis. Only hashes persist; downloaded bytes live in a temporary directory."""
import hashlib
import math
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request
from PIL import Image, ImageOps, UnidentifiedImageError
import json
import uuid
import urllib.error
from app.services.telegram import APIError, NetworkError, DeferredRequest
from app.services.monitoring import safe_error

class MediaTooLarge(ValueError):pass

MAX_BYTES=20*1024*1024
Image.MAX_IMAGE_PIXELS=25_000_000
COS=[[math.cos((2*x+1)*u*math.pi/64) for x in range(32)] for u in range(8)]


def phash(path):
    with Image.open(path) as im:
        im=ImageOps.exif_transpose(im).convert('L').resize((32,32),Image.Resampling.LANCZOS)
        px=list(im.get_flattened_data() if hasattr(im,'get_flattened_data') else im.getdata())
    horizontal=[[sum(px[y*32+x]*COS[u][x] for x in range(32)) for u in range(8)] for y in range(32)]
    coeff=[sum(horizontal[y][u]*COS[v][y] for y in range(32)) for v in range(8) for u in range(8)]
    median=sorted(coeff[1:])[31]
    result=0
    for i,c in enumerate(coeff):
        if i and c>median: result |= 1<<i
    return f'{result:016x}'


def distance(a,b): return (int(a,16)^int(b,16)).bit_count()


def bands(value):
    return [value[i:i+2] for i in range(0,16,2)]


class MediaEngine:
    def __init__(self,db,api): self.db,self.api=db,api

    def candidates(self,value):
        result=set()
        for i,band in enumerate(bands(value)):
            # At <=6 differing bits among 8 bands, at least one full band is equal.
            for row in self.db.all('SELECT hash FROM hash_bands WHERE band=? AND value=?',(i,band)):
                result.add(row['hash'])
        return [h for h in result if distance(h,value)<=6]

    def add(self,fid,sha,hashes,gid):
        self.db.run('INSERT INTO media_files(file_id,sha,gid) VALUES (?,?,?) ON CONFLICT DO NOTHING',(fid,sha,gid))
        for value in hashes:
            self.db.run('INSERT INTO media_hashes(file_id,hash) VALUES (?,?) ON CONFLICT DO NOTHING',(fid,value))
            for i,band in enumerate(bands(value)):
                self.db.run('INSERT INTO hash_bands(hash,band,value) VALUES (?,?,?) ON CONFLICT DO NOTHING',(value,i,band))

    def analyze(self,task):
        if self.db.one('SELECT 1 FROM fingerprints WHERE file_id=? AND blocked=1',(task['file_id'],)):return True
        known=self.db.one('SELECT sha FROM media_files WHERE file_id=?',(task['file_id'],))
        if known:
            hashes=[r['hash'] for r in self.db.all('SELECT hash FROM media_hashes WHERE file_id=?',(task['file_id'],))]
            if hashes:return self.matches(task,known['sha'],hashes)
        info=self.api.call('getFile',file_id=task['download_id'])
        if info.get('file_size',0)>MAX_BYTES: raise MediaTooLarge('Fichier au-delà de la limite de téléchargement de cette API Telegram.')
        with tempfile.TemporaryDirectory(prefix='bot-media-') as tmp:
            source=Path(tmp)/'source'
            base=self.api.base.replace('/bot','/file/bot')
            request=urllib.request.Request(base+info['file_path'])
            with urllib.request.urlopen(request,timeout=30) as response:
                data=response.read(MAX_BYTES+1)
            if len(data)>MAX_BYTES: raise MediaTooLarge('Fichier trop volumineux pour cette analyse.')
            sha=hashlib.sha256(data).hexdigest()
            source.write_bytes(data)
            del data
            hashes=[]
            if task['kind']=='photo': hashes=[phash(source)]
            else:
                probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(source)],capture_output=True,text=True,timeout=10,check=True)
                duration=float(probe.stdout.strip())
                if not math.isfinite(duration) or duration<=0: raise ValueError('Durée vidéo invalide.')
                for i,fraction in enumerate((0.1,0.5,0.9)):
                    target=Path(tmp)/f'{i}.png'
                    subprocess.run(['ffmpeg','-v','error','-threads','1','-filter_threads','1','-ss',str(duration*fraction),'-i',str(source),'-frames:v','1','-vf','scale=128:128:force_original_aspect_ratio=decrease','-y',str(target)],capture_output=True,timeout=15,check=True)
                    hashes.append(phash(target))
            delete=self.matches(task,sha,hashes)
            with self.db.transaction():self.add(task['file_id'],sha,hashes,task['gid'])
            return delete

    def matches(self,task,sha,hashes):
        exact=self.db.all('SELECT * FROM media_files WHERE sha=? AND file_id!=?',(sha,task['file_id']))
        matches={r['file_id'] for r in exact}
        votes={}
        for h in hashes:
            matched_files=set()
            for candidate in self.candidates(h):
                matched_files.update(r['file_id'] for r in self.db.all('SELECT file_id FROM media_hashes WHERE hash=?',(candidate,)))
            for fid in matched_files: votes[fid]=votes.get(fid,0)+1
        matches.update(fid for fid,n in votes.items() if n >= (2 if task['kind']=='video' else 1))
        matches.discard(task['file_id'])
        delete=False
        for fid in matches:
            blocked=self.db.one('SELECT 1 FROM fingerprints WHERE file_id=? AND blocked=1',(fid,))
            same_group=self.db.one('SELECT 1 FROM media_files WHERE file_id=? AND gid=?',(fid,task['gid']))
            if blocked or (task['anti'] and same_group): delete=True; break
        return delete


def media_error(error):
    text=str(error).lower()
    if isinstance(error,MediaTooLarge) or isinstance(error,APIError) and 'file is too big' in text:
        return 'size','Fichier trop volumineux : analyse visuelle indisponible via cette API Telegram (limite 20 MB).'
    if isinstance(error,subprocess.TimeoutExpired):return 'timeout','Décodage vidéo trop long ; analyse interrompue pour libérer le worker.'
    if isinstance(error,(NetworkError,urllib.error.URLError,TimeoutError,ConnectionError)) or isinstance(error,APIError) and (error.code>=500 or error.code==429):
        return 'network',safe_error(error)
    if isinstance(error,(UnidentifiedImageError,Image.DecompressionBombError,subprocess.CalledProcessError,ValueError)):
        return 'format','Fichier illisible, format non décodable ou dimensions/durée invalides ('+type(error).__name__+').'
    return 'error',safe_error(error)


def process_media_one(db,engine):
    task=db.one('SELECT t.*,g.chat,g.anti FROM media_tasks t JOIN groups g ON t.gid=g.id WHERE t.due<=? AND g.enabled=1 ORDER BY t.due LIMIT 1',(time.time(),))
    if not task:return False
    try:
        if engine.analyze(task):
            db.run('INSERT INTO jobs(id,method,payload,due) VALUES (?,?,?,?)',
                   (uuid.uuid4().hex,'deleteMessage',json.dumps({'chat_id':task['chat'],'message_id':task['mid']}),time.time()))
        db.run('DELETE FROM media_tasks WHERE id=?',(task['id'],))
        db.run('DELETE FROM media_failures WHERE id=?',(task['id'],))
        db.run('DELETE FROM failed_actions WHERE id=?',(task['id'],))
    except Exception as error:
        if isinstance(error,DeferredRequest) or isinstance(error,APIError) and error.code==429:
            db.run('UPDATE media_tasks SET due=? WHERE id=?',(time.time()+max(1,error.retry),task['id']));return True
        category,detail=media_error(error);attempts=task['attempts']+1
        transient=category in ('network','timeout')
        if transient and attempts<5:
            db.run('UPDATE media_tasks SET attempts=?,due=? WHERE id=?',(attempts,time.time()+min(1800,15*2**attempts),task['id']))
        else:
            detail='Groupe '+task['gid']+' · message '+str(task['mid'])+' · '+detail
            with db.transaction():
                db.run('INSERT INTO media_failures(id,payload,category,detail,created) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,category=excluded.category,detail=excluded.detail,created=excluded.created',
                       (task['id'],json.dumps(task),category,detail,time.time()))
                db.run('INSERT INTO failed_actions(id,kind,detail,created) VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET detail=excluded.detail,created=excluded.created',
                       (task['id'],'media:'+category,detail,time.time()))
                db.run('DELETE FROM media_tasks WHERE id=?',(task['id'],))
            # Expected API/file limitations stay in health, not hourly alarm floods.
            if category in ('network','timeout','error'):
                stamp=float(db.setting('media:alert_at:'+category,'0'))
                if time.time()-stamp>86400:
                    db.set('media:alert_at:'+category,time.time())
                    db.set('media_warning','Analyses médias à examiner : '+category+'. Les autres médias continuent ; détails et reprise dans État du réseau.')
    return True


def retry_media_failures(db,limit=20):
    count=0
    for row in db.all("SELECT * FROM media_failures WHERE category IN ('network','timeout') ORDER BY created LIMIT ?",(limit,)):
        task=json.loads(row['payload']);g=db.one('SELECT * FROM groups WHERE id=?',(task['gid'],))
        if not g or not g['enabled'] or g['chat']!=task['chat']:continue
        with db.transaction():
            db.run('INSERT INTO media_tasks(id,gid,mid,file_id,download_id,kind,due) VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                   (task['id'],task['gid'],task['mid'],task['file_id'],task['download_id'],task['kind'],time.time()))
            db.run('DELETE FROM media_failures WHERE id=?',(row['id'],))
            db.run('DELETE FROM failed_actions WHERE id=?',(row['id'],))
        count+=1
    return count


def run_worker(url,token,stop):
    from app.db import DB
    from .telegram import Telegram
    db=DB(url,lock=False,initialize=False);engine=MediaEngine(db,Telegram(token,background=True))
    try:
        while not stop.is_set():
            db.set('media:heartbeat',time.time())
            if not process_media_one(db,engine):stop.wait(.5)
    finally:db.conn.close()
