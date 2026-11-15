"""Bounded, temporary media analysis. No permanent media-file archive."""
import hashlib
import io
import json
import subprocess
import tempfile
import uuid
from pathlib import Path


def bands(h):
    # 8 disjoint 8-bit bands: Hamming <= 7 guarantees at least one shared band.
    return [(i,h[i*2:i*2+2]) for i in range(8)]

def near(a,b):
    return (int(a,16)^int(b,16)).bit_count()<=6

class Media:
    def __init__(self,s):self.s=s

    def fingerprints(self,data,kind):
        import imagehash
        from PIL import Image
        def ph(raw):
            with Image.open(raw) as im:
                return str(imagehash.phash(im.convert('RGB')))
        if kind=='photo':return [ph(io.BytesIO(data))]
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'input.mp4';p.write_bytes(data)
            probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(p)],capture_output=True,timeout=10,check=True)
            duration=float(probe.stdout)
            hashes=[]
            for i in (0.1,0.35,0.6,0.85):
                target=Path(folder)/'frame.png'
                subprocess.run(['ffmpeg','-v','error','-y','-ss',str(duration*i),'-i',str(p),'-frames:v','1','-vf','scale=320:-1',str(target)],capture_output=True,timeout=15,check=True)
                hashes.append(ph(target))
            return hashes

    def inspect(self,g,m,force=False):
        s=self.s;db=s.db
        f=m.get('video') or (m.get('photo') or [None])[-1]
        if not f:return False
        kind='video' if m.get('video') else 'photo'
        uid=m['from']['id'];mid=m['message_id']
        db.run('INSERT INTO media(gid,mid,uid,file_id,unique_id,kind) VALUES(?,?,?,?,?,?) ON CONFLICT(gid,mid) DO NOTHING',(g['id'],mid,uid,f['file_id'],f['file_unique_id'],kind))
        from .domain import config
        dedup=config(g)['dedup']
        matches=db.all('SELECT * FROM fingerprints WHERE unique_id=?',(f['file_unique_id'],))
        if not force and any(r['blocked'] or dedup for r in matches):return True
        try:
            data=s.tg.download(f['file_id'])
            sha=hashlib.sha256(data).hexdigest()
            matches=db.all('SELECT * FROM fingerprints WHERE sha=?',(sha,))
            if not force and any(r['blocked'] or dedup for r in matches):return True
            hashes=self.fingerprints(data,kind)
        except Exception as e:
            s.alert('media:'+g['id'],g['name']+' : analyse de média incomplète (taille, format ou délai). Média retiré par précaution ; vérifier les limites dans le guide.')
            return True
        candidates={}
        for h in hashes:
            for band,val in bands(h):
                for r in db.all('SELECT f.* FROM fingerprints f JOIN hash_bands b ON f.id=b.fid WHERE b.band=? AND b.value=?',(band,val)):
                    if r['blocked'] or dedup:candidates[r['id']]=r
        for r in candidates.values():
            old=json.loads(r['phashes'])
            needed=1 if kind=='photo' else 3
            if not force and sum(any(near(h,o) for o in old) for h in hashes)>=needed:return True
        fid=uuid.uuid4().hex
        db.run('INSERT INTO fingerprints VALUES(?,?,?,?,?)',(fid,f['file_unique_id'],sha,json.dumps(hashes),int(force)))
        for h in hashes:
            for band,val in bands(h):db.run('INSERT INTO hash_bands VALUES(?,?,?) ON CONFLICT DO NOTHING',(band,val,fid))
        db.run('UPDATE media SET sha=?,phashes=? WHERE gid=? AND mid=?',(sha,json.dumps(hashes),g['id'],mid))
        return False

    def blacklist(self,gid,mids):
        db=self.s.db
        for mid in mids:
            r=db.one('SELECT * FROM media WHERE gid=? AND mid=?',(gid,mid))
            if not r:continue
            matches=db.all('SELECT id FROM fingerprints WHERE unique_id=? OR sha=?',(r['unique_id'],r['sha']))
            if matches:
                for x in matches:db.run('UPDATE fingerprints SET blocked=1 WHERE id=?',(x['id'],))
            else:
                db.run('INSERT INTO fingerprints VALUES(?,?,?,?,1)',(uuid.uuid4().hex,r['unique_id'],r['sha'],r['phashes'] or '[]'))
