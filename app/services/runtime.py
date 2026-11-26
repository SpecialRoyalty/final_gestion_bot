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
from app.services.telegram import Telegram, APIError, NetworkError, blocked_recipient, DeferredRequest

PRIVATE_LANES=4
MODERATION_LANES=2
_WAKE_LOCK=threading.Lock()
_WAKE={}

def wake_event(lane):
    with _WAKE_LOCK:return _WAKE.setdefault(lane,threading.Event())

def lane_for(update):
    q=update.get('callback_query')
    if q:return 'private:'+str(q['from']['id']%PRIVATE_LANES)
    m=update.get('message') or update.get('edited_message')
    if m:
        if m['chat']['type']=='private':return 'private:'+str(m['from']['id']%PRIVATE_LANES)
        text=m.get('text',m.get('caption','')).strip().casefold()
        if text and text.split(maxsplit=1)[0].split('@')[0]=='/pedo':return 'urgent:'+str(abs(m['chat']['id'])%2)
        if m.get('reply_to_message') and '@admin' in text:return 'urgent:'+str(abs(m['chat']['id'])%2)
        if m.get('new_chat_members') or m.get('left_chat_member'):return 'service'
        return 'moderation:'+str(abs(m['chat']['id'])%MODERATION_LANES)
    if 'pre_checkout_query' in update:return 'private:'+str(update['pre_checkout_query']['from']['id']%PRIVATE_LANES)
    if 'chat_join_request' in update:return 'admission:'+str(update['chat_join_request']['from']['id']%2)
    event=update.get('chat_member') or update.get('my_chat_member') or update.get('chat_join_request') or {}
    return 'moderation:'+str(abs(event.get('chat',{}).get('id',0))%MODERATION_LANES)

def actor_for(update):
    q=update.get('callback_query')
    if q:return 'user:'+str(q['from']['id'])
    m=update.get('message') or update.get('edited_message')
    if m:return ('user:'+str(m['from']['id'])) if m['chat']['type']=='private' else 'chat:'+str(m['chat']['id'])
    if 'chat_join_request' in update:return 'join:'+str(update['chat_join_request']['chat']['id'])+':'+str(update['chat_join_request']['from']['id'])
    if 'pre_checkout_query' in update:return 'user:'+str(update['pre_checkout_query']['from']['id'])
    e=update.get('chat_member') or update.get('my_chat_member') or update.get('chat_join_request') or {}
    return 'chat:'+str(e.get('chat',{}).get('id',0))

def restore_actors(db):
    for row in db.all("SELECT id,payload FROM update_inbox WHERE state='ready'"):
        update=json.loads(row['payload'])
        db.run('INSERT INTO update_actors(id,actor) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET actor=excluded.actor',(row['id'],actor_for(update)))
        db.run('UPDATE update_inbox SET lane=? WHERE id=?',(lane_for(update),row['id']))

def persist_updates(db,updates):
    """The Telegram offset advances only in the transaction that stores every update."""
    with db.transaction():
        for update in updates:
            db.run('INSERT INTO update_inbox(id,lane,payload,created) VALUES (?,?,?,?) ON CONFLICT DO NOTHING',
                   (update['update_id'],lane_for(update),json.dumps(update),time.time()))
            db.run('INSERT INTO update_actors(id,actor) VALUES (?,?) ON CONFLICT DO NOTHING',(update['update_id'],actor_for(update)))
        if updates:db.set('offset',max(u['update_id'] for u in updates)+1)
    for lane in {lane_for(update) for update in updates}:wake_event(lane).set()

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
    row=db.one("SELECT u.* FROM update_inbox u LEFT JOIN update_actors a ON a.id=u.id WHERE u.lane=? AND u.state='ready' AND u.due<=? AND NOT EXISTS (SELECT 1 FROM update_inbox earlier LEFT JOIN update_actors ea ON ea.id=earlier.id WHERE earlier.lane=u.lane AND earlier.state='ready' AND earlier.id<u.id AND (ea.actor=a.actor OR a.actor IS NULL)) ORDER BY u.id LIMIT 1",(lane,time.time()))
    if not row or row['due']>time.time():return False
    update=json.loads(row['payload']);started=time.monotonic();queue_ms=round(max(0,time.time()-row['created'])*1000);state='done'
    message=update.get('message',{})
    recipient=update.get('callback_query',{}).get('from',{}).get('id') or (message.get('from',{}).get('id') if message.get('chat',{}).get('type')=='private' else None)
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
        if blocked_recipient(error) and recipient:
            db.set('dm_blocked:'+str(recipient),time.time())
            db.run("UPDATE update_inbox SET state='done' WHERE id=?",(row['id'],));return True
        # Retry a bounded number of times, retaining per-lane event ordering.
        if isinstance(error,DeferredRequest):
            db.run('UPDATE update_inbox SET due=? WHERE id=?',(time.time()+error.retry,row['id']));return True
        attempts=row['attempts']+1
        if isinstance(error,(NetworkError,APIError)) and (getattr(error,'code',0)==429 or getattr(error,'code',0)>=500 or isinstance(error,NetworkError)) and attempts<3:
            db.run('UPDATE update_inbox SET attempts=?,due=? WHERE id=?',(attempts,time.time()+max(getattr(error,'retry',0),min(5,attempts)),row['id']))
            return True
        state='failed';app.record_failure('update:'+lane,error)
    db.run('UPDATE update_inbox SET state=? WHERE id=?',(state,row['id']))
    db.set('latency:'+lane+':ms',round((time.monotonic()-started)*1000))
    db.set('latency:'+lane+':queue_ms',queue_ms)
    db.set('worker:'+lane+':ok',time.time())
    return True

def run_lane(url,token,owners,tz,support,me,stop,lane):
    db=None
    try:
        db=DB(url,lock=False,initialize=False)
        app=App(db,Telegram(token,timeout=4,retries=1),owners,tz,support,me=me)
        wake=wake_event(lane)
        while not stop.is_set():
            wake.clear()
            try:
                if not process_one(app,lane):wake.wait(.25)
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
        app=App(db,Telegram(token,timeout=6,retries=1,background=True),owners,tz,support,me=me)
        app.external_jobs=True
        if kind=='portal':
            db.run("UPDATE portal_recipients SET state='uncertain' WHERE state='sending'")
            db.run("UPDATE feature_notifications SET state='uncertain' WHERE state='sending'")
        while not stop.is_set():
            try:
                if kind=='jobs':app.jobs()
                elif kind=='portal':
                    app.feature_notification_step()
                    app.portal_move_step()
                else:app.tick()
                db.set('worker:'+kind+':ok',time.time())
            except Exception as error:
                try:app.record_failure('worker:'+kind,error)
                except Exception:logging.error('Worker %s: %s',kind,type(error).__name__)
            stop.wait(.1 if kind in ('jobs','portal') else .5)
    finally:
        if db:db.conn.close()

def start_workers(url,token,owners,tz,support,me,stop):
    workers=[]
    for lane in ['private:'+str(i) for i in range(PRIVATE_LANES)]+['moderation:'+str(i) for i in range(MODERATION_LANES)]+['service','admission:0','admission:1','urgent:0','urgent:1']:
        workers.append(threading.Thread(target=run_lane,args=(url,token,owners,tz,support,me,stop,lane),name=lane,daemon=True))
    for kind in ('jobs','scheduler','portal'):
        workers.append(threading.Thread(target=run_background,args=(url,token,owners,tz,support,me,stop,kind),name=kind,daemon=True))
    for worker in workers:worker.start()
    return workers
