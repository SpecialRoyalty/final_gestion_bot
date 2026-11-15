import json
import logging
import multiprocessing
import os
import signal
import time
from .db import DB
from .domain import now
from .service import Service
from .scheduler import Scheduler
from .events import Events
from .ui import UI
from .telegram import Telegram,TelegramError

LOG=logging.getLogger('bot')


def media_worker(database,token,owners,bot_id):
    from .media import Media
    db=DB(database);tg=Telegram(token);s=Service(db,tg,owners,bot_id);media=Media(s)
    if db.pg and not db.one('SELECT pg_try_advisory_lock(8739012) AS locked')['locked']:
        raise RuntimeError('Un worker média tourne déjà.')
    while True:
        jobs=db.all('SELECT * FROM media_queue WHERE due<=? ORDER BY due LIMIT 5',(now(),))
        for job in jobs:
            try:
                g=s.group(job['gid']);m=json.loads(job['body']);uid=m['from']['id']
                ban=s.banned(uid)
                if media.inspect(g,m,force=bool(ban and ban['permanent'])) or ban:
                    s.enqueue(f'del:{g["id"]}:{m["message_id"]}','delete',{'gid':g['id'],'mid':m['message_id']})
                else:db.run('UPDATE membership SET last_media=?,warned=0 WHERE gid=? AND uid=?',(now(),g['id'],uid))
                # A moderation command can arrive while hashing is in flight.
                if s.banned(uid) and s.banned(uid)['permanent']:media.blacklist(g['id'],[m['message_id']])
                db.run('DELETE FROM media_queue WHERE id=?',(job['id'],))
            except Exception:
                db.run('UPDATE media_queue SET attempts=attempts+1,due=? WHERE id=?',(now()+60,job['id']))
                s.alert('media-worker:'+job['gid'],'Analyse média en attente après une erreur. Nouvelle tentative programmée.')
        time.sleep(1)


def scheduler_worker(database,token,owners,bot_id,username,tz):
    db=DB(database)
    if db.pg and not db.one('SELECT pg_try_advisory_lock(8739013) AS locked')['locked']:
        raise RuntimeError('Un ordonnanceur tourne déjà.')
    s=Service(db,Telegram(token),owners,bot_id);scheduler=Scheduler(s,username,tz)
    while True:
        try:scheduler.tick()
        except TelegramError:time.sleep(5)
        time.sleep(1)


def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
    token=os.environ['BOT_TOKEN'];database=os.environ['DATABASE_URL']
    owners={int(x.strip()) for x in os.environ['OWNER_IDS'].split(',') if x.strip()}
    if not owners:raise RuntimeError('OWNER_IDS doit contenir au moins un identifiant Telegram.')
    if not database.startswith(('postgres://','postgresql://')):raise RuntimeError('PostgreSQL requis en production.')
    db=DB(database);db.init()
    if not db.one('SELECT pg_try_advisory_lock(8739011) AS locked')['locked']:
        raise RuntimeError('Une autre instance est active. Utilise une seule réplique Railway.')
    tg=Telegram(token);me=tg.call('getMe');s=Service(db,tg,owners,me['id'])
    sch=Scheduler(s,me['username'],os.environ.get('TZ','Europe/Paris'));events=Events(s);ui=UI(s,sch,me['username'])
    if db.setting('sales') is None:db.set('sales',False)
    tg.call('deleteWebhook',drop_pending_updates=False)
    tg.call('deleteMyCommands')
    worker=multiprocessing.Process(target=media_worker,args=(database,token,owners,me['id']),daemon=True);worker.start()
    clock=multiprocessing.Process(target=scheduler_worker,args=(database,token,owners,me['id'],me['username'],sch.tz),daemon=True);clock.start()
    running=True
    def stop(*_):
        nonlocal running
        running=False
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        while running:
            if not worker.is_alive() or not clock.is_alive():raise RuntimeError('Worker arrêté ; redémarrage requis.')
            try:
                offset=db.setting('offset',0)
                updates=tg.call('getUpdates',offset=offset,timeout=1,allowed_updates=['message','edited_message','callback_query','my_chat_member','chat_member','chat_join_request','pre_checkout_query'])
                for update in updates:
                    db.run('INSERT INTO inbox(id,body) VALUES(?,?) ON CONFLICT DO NOTHING',(update['update_id'],json.dumps(update)))
                    db.set('offset',update['update_id']+1)
                rows=db.all('SELECT * FROM inbox WHERE due<=? ORDER BY id LIMIT 100',(now(),))
                rows.sort(key=lambda r: 0 if 'pre_checkout_query' in json.loads(r['body']) else 1)
                for row in rows:
                    u=json.loads(row['body'])
                    try:
                        handle(u,s,ui,events)
                        db.run('DELETE FROM inbox WHERE id=?',(row['id'],))
                    except (ValueError,KeyError,TypeError) as e:
                        # Invalid user input does not poison the inbox; payments are never dropped here.
                        if u.get('message',{}).get('successful_payment'):raise
                        actor=u.get('callback_query',{}).get('from') or u.get('message',{}).get('from')
                        if actor and db.one('SELECT id FROM users WHERE id=? AND started=1',(actor['id'],)):
                            ui.show(actor['id'],'ℹ️ '+str(e),[[{'text':'🏠 Accueil','callback_data':'home'}]])
                        db.run('DELETE FROM inbox WHERE id=?',(row['id'],))
                    except Exception as e:
                        db.run('UPDATE inbox SET attempts=attempts+1,due=? WHERE id=?',(now()+min(300,2**min(row['attempts']+1,8)),row['id']))
                        if row['attempts']==2:s.alert('update:'+str(row['id']),'Événement Telegram en erreur : '+type(e).__name__+'. Nouvelle tentative conservée.')
            except TelegramError as e:
                LOG.warning('API Telegram indisponible (%s)',e.code)
                time.sleep(max(1,min(e.retry,30)))
            except Exception as e:
                LOG.error('Boucle interrompue : %s',type(e).__name__)
                raise
    finally:
        worker.terminate();clock.terminate();worker.join(timeout=5);clock.join(timeout=5);db.conn.close()


def handle(u,s,ui,events):
    db=s.db
    if 'pre_checkout_query' in u:
        q=u['pre_checkout_query'];error=None
        try:
            if not db.setting('sales',False):raise ValueError('Ventes temporairement suspendues.')
            o=db.one('SELECT * FROM orders WHERE id=?',(q['invoice_payload'],))
            s.validate_order(o,q['from']['id'],q['total_amount'],q['currency'])
            if o['status']=='checkout' and o['approved_until']>now():raise ValueError('Un paiement est déjà en cours.')
            db.run("UPDATE orders SET status='checkout',approved_until=? WHERE id=?",(now()+900,o['id']))
        except ValueError as e:error=str(e)
        data={'pre_checkout_query_id':q['id'],'ok':not error}
        if error:data['error_message']=error
        s.tg.call('answerPreCheckoutQuery',**data);return
    if 'callback_query' in u:
        q=u['callback_query'];uid=q['from']['id'];s.upsert_user(q['from'],True)
        # Ignore expired callback acknowledgements but still update the canonical menu.
        try:s.tg.call('answerCallbackQuery',callback_query_id=q['id'])
        except TelegramError as e:
            if e.code!=400:raise
        ui.action(uid,q.get('data','home'));return
    for key in ('my_chat_member','chat_member'):
        if key in u:events.member(u[key]);return
    if 'chat_join_request' in u:events.join_request(u['chat_join_request']);return
    m=u.get('message') or u.get('edited_message')
    if not m:return
    if m['chat']['type']!='private':events.message(m);return
    uid=m['from']['id'];s.upsert_user(m['from'],True)
    if m.get('successful_payment'):
        s.paid(uid,m['successful_payment']);ui.render(uid,'access');return
    text=m.get('text','')
    if text.startswith('/start'):
        payload=text.split(maxsplit=1)[1] if len(text.split(maxsplit=1))>1 else 'home'
        screen='g:'+payload[2:] if payload.startswith('g_') else payload if payload in ('home','invite','access','cart') else 'home'
        ui.render(uid,screen)
    elif text.startswith('/paysupport'):ui.render(uid,'help')
    else:ui.input(uid,m)

if __name__=='__main__':main()
