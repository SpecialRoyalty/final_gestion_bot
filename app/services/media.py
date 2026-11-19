"""Bounded media analysis. Only hashes persist; downloaded bytes live in a temporary directory."""
import hashlib
import math
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request
from PIL import Image, ImageOps

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
        if info.get('file_size',0)>MAX_BYTES: raise ValueError('Média supérieur à 20 Mio : analyse avancée indisponible sur cette API.')
        with tempfile.TemporaryDirectory(prefix='bot-media-') as tmp:
            source=Path(tmp)/'source'
            base=self.api.base.replace('/bot','/file/bot')
            request=urllib.request.Request(base+info['file_path'])
            with urllib.request.urlopen(request,timeout=30) as response:
                data=response.read(MAX_BYTES+1)
            if len(data)>MAX_BYTES: raise ValueError('Média trop volumineux.')
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
                    subprocess.run(['ffmpeg','-v','error','-threads','1','-ss',str(duration*fraction),'-i',str(source),'-frames:v','1','-vf','scale=128:128:force_original_aspect_ratio=decrease','-y',str(target)],capture_output=True,timeout=15,check=True)
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


def run_worker(url,token,stop):
    # Independent DB connection: slow media decoding never blocks payment callbacks.
    import json
    import uuid
    from app.db import DB
    from .telegram import Telegram, DeferredRequest
    db=DB(url,lock=False,initialize=False); api=Telegram(token,background=True); engine=MediaEngine(db,api)
    while not stop.is_set():
        db.set('media:heartbeat',time.time())
        task=db.one('SELECT t.*,g.chat,g.anti FROM media_tasks t JOIN groups g ON t.gid=g.id WHERE t.due<=? ORDER BY t.due LIMIT 1',(time.time(),))
        if not task:
            stop.wait(2); continue
        try:
            if engine.analyze(task):
                db.run('INSERT INTO jobs(id,method,payload,due) VALUES (?,?,?,?)',
                       (uuid.uuid4().hex,'deleteMessage',json.dumps({'chat_id':task['chat'],'message_id':task['mid']}),time.time()))
            db.run('DELETE FROM media_tasks WHERE id=?',(task['id'],))
        except Exception as e:
            # No raw error/URL persisted, since download URLs include the bot token.
            if isinstance(e,DeferredRequest):
                db.run('UPDATE media_tasks SET due=? WHERE id=?',(time.time()+e.retry,task['id']));continue
            attempts=task['attempts']+1
            if attempts>=5 or isinstance(e,ValueError):
                db.run('INSERT INTO failed_actions(id,kind,detail,created) VALUES (?,?,?,?) ON CONFLICT DO NOTHING',(task['id'],'media',type(e).__name__+' : groupe '+task['gid'],time.time()))
                db.run('DELETE FROM media_tasks WHERE id=?',(task['id'],))
                db.set('media_warning','Analyses médias arrêtées après échec. Consulter l’état du réseau.');continue
            db.run('UPDATE media_tasks SET attempts=?,due=? WHERE id=?',(attempts,time.time()+min(86400,60*2**min(attempts,10)),task['id']))
            if attempts==3:db.set('media_warning','Analyses médias retardées : des reprises sont en cours. Consulter l’état du réseau.')
    db.conn.close()
