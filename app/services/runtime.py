"""Durable polling inbox with isolated, ordered worker lanes.
One polling owner; one DB connection and one application instance per worker.
"""
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from app.application import App
from app.db import DB
from app.services.telegram import Telegram, APIError, NetworkError

PRIVATE_LANES=4
MODERATION_LANES=2

def lane_for(update):
    q=update.get('callback_query')
    if q:return 'private:'+str(q['from']['id']%PRIVATE_LANES)
    m=update.get('message') or update.get('edited_message')
    if m:
        if m['chat']['type']=='private':return 'private:'+str(m['from']['id']%PRIVATE_LANES)
        if m.get('new_chat_members') or m.get('left_chat_member'):return 'service'
        return 'moderation:'+str(abs(m['chat']['id'])%MODERATION_LANES)
    if 'pre_checkout_query' in update:return 'private:'+str(update['pre_checkout_query']['from']['id']%PRIVATE_LANES)
    event=update.get('chat_member') or update.get('my_chat_member') or update.get('chat_join_request') or {}
    return 'moderation:'+str(abs(event.get('chat',{}).get('id',0))%MODERATION_LANES)

def persist_updates(db,updates):
    """The Telegram offset advances only in the transaction that stores every update."""
    with db.transaction():
        for update in updates:
            db.run('INSERT INTO update_inbox(id,lane,payload,created) VALUES (?,?,?,?) ON CONFLICT DO NOTHING',
                   (update['update_id'],lane_for(update),json.dumps(update),time.time()))
        if updates:db.set('offset',max(u['update_id'] for u in updates)+1)

class Acknowledgements:
    def __init__(self,api):
        self.api=api;self.pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='callback-ack')
        self.lock=threading.Lock();self.errors=0
    def submit(self,update):
        q=update.get('callback_query')
        if not q:return
        q['_acknowledged']=True
        self.pool.submit(self.answer,q['id'])
    def answer(self,qid):
        try:self.api.call('answerCallbackQuery',callback_query_id=qid)
        except Exception:
            with self.lock:self.errors+=1
            # Aggregated telemetry only: expired spinners do not cancel actions or spam owners.
    def close(self):self.pool.shutdown(wait=True,cancel_futures=True)

def process_one(app,lane):
    db=app.db
    row=db.one("SELECT * FROM update_inbox WHERE lane=? AND state='ready' ORDER BY id LIMIT 1",(lane,))
    if not row or row['due']>time.time():return False
    update=json.loads(row['payload']);started=time.monotonic();state='done'
    try:
        app.update(update)
    except ValueError as error:
        m=update.get('message',{});q=update.get('callback_query',{})
        uid=m.get('from',{}).get('id') if m.get('chat',{}).get('type')=='private' else q.get('from',{}).get('id')
        if uid:
            try:app.screen(uid,str(error),[[{'text':app.tr(uid,'⬅️ Accueil'),'callback_data':'home'}]])
            except Exception as delivery_error:
                state='failed';app.record_failure('update:'+lane,delivery_error)
        else:app.record_failure('update:'+lane,error)
    except Exception as error:
        # Retry a bounded number of times, retaining per-lane event ordering.
        attempts=row['attempts']+1
        if isinstance(error,(NetworkError,APIError)) and (getattr(error,'code',0)==429 or getattr(error,'code',0)>=500 or isinstance(error,NetworkError)) and attempts<3:
            db.run('UPDATE update_inbox SET attempts=?,due=? WHERE id=?',(attempts,time.time()+max(getattr(error,'retry',0),min(5,attempts)),row['id']))
            return True
        state='failed';app.record_failure('update:'+lane,error)
    db.run('UPDATE update_inbox SET state=? WHERE id=?',(state,row['id']))
    db.set('latency:'+lane+':ms',round((time.monotonic()-started)*1000))
    db.set('latency:'+lane+':queue_ms',round(max(0,time.time()-row['created'])*1000))
    db.set('worker:'+lane+':ok',time.time())
    return True

def run_lane(url,token,owners,tz,support,me,stop,lane):
    db=None
    try:
        db=DB(url,lock=False,initialize=False)
        app=App(db,Telegram(token,timeout=4,retries=1),owners,tz,support,me=me)
        while not stop.is_set():
            try:
                if not process_one(app,lane):stop.wait(.05)
            except Exception as error:
                try:app.record_failure('worker:'+lane,error)
                except Exception:logging.error('Worker %s: %s',lane,type(error).__name__)
                stop.wait(.5)
    finally:
        if db:db.conn.close()

def run_background(url,token,owners,tz,support,me,stop,kind):
    db=None
    try:
        db=DB(url,lock=False,initialize=False)
        app=App(db,Telegram(token,timeout=6,retries=1),owners,tz,support,me=me)
        app.external_jobs=True
        while not stop.is_set():
            try:
                app.jobs() if kind=='jobs' else app.tick()
                db.set('worker:'+kind+':ok',time.time())
            except Exception as error:
                try:app.record_failure('worker:'+kind,error)
                except Exception:logging.error('Worker %s: %s',kind,type(error).__name__)
            stop.wait(.1 if kind=='jobs' else .5)
    finally:
        if db:db.conn.close()

def start_workers(url,token,owners,tz,support,me,stop):
    workers=[]
    for lane in ['private:'+str(i) for i in range(PRIVATE_LANES)]+['moderation:'+str(i) for i in range(MODERATION_LANES)]+['service']:
        workers.append(threading.Thread(target=run_lane,args=(url,token,owners,tz,support,me,stop,lane),name=lane,daemon=True))
    for kind in ('jobs','scheduler'):
        workers.append(threading.Thread(target=run_background,args=(url,token,owners,tz,support,me,stop,kind),name=kind,daemon=True))
    for worker in workers:worker.start()
    return workers
