import logging
import os
import time
import threading
from .db import DB, SchemaMismatch
from .services.telegram import Telegram
from .application import App
from .services.runtime import Acknowledgements, persist_updates, start_workers

def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    token=os.environ['BOT_TOKEN'];url=os.environ['DATABASE_URL']
    owners={int(x.strip()) for x in os.environ['OWNER_IDS'].split(',') if x.strip()}
    if not owners:raise RuntimeError('OWNER_IDS requis')
    try:db=DB(url)
    except SchemaMismatch as error:
        logging.error('%s',error);raise SystemExit(2) from None
    api=Telegram(token,timeout=5,retries=1)
    tz=os.getenv('TIMEZONE','Europe/Paris');support=os.getenv('SUPPORT_CONTACT','Administrateur')
    app=App(db,api,owners,tz,support)
    api.call('deleteWebhook',drop_pending_updates=False);app.install_command_menu()
    allowed=['message','edited_message','callback_query','chat_member','my_chat_member','chat_join_request','pre_checkout_query']
    stop=threading.Event();acks=Acknowledgements(Telegram(token,timeout=2,retries=1))
    workers=start_workers(url,token,owners,tz,support,app.me,stop)
    from .services.media import run_worker
    media=threading.Thread(target=run_worker,args=(url,token,stop),daemon=True);media.start();workers.append(media)
    logging.info('Bot démarré : boutons, modération, arrivées/départs et tâches automatiques séparés.')
    cleanup=0;poll_failures=0
    try:
        while not stop.is_set():
            try:
                if any(not w.is_alive() for w in workers):raise RuntimeError('Worker arrêté : redémarrage du processus requis.')
                updates=api.call('getUpdates',offset=int(db.setting('offset','0')),timeout=10,allowed_updates=allowed)
                for update in updates:acks.submit(update)
                persist_updates(db,updates)
                db.set('poll:ok',time.time());db.set('callback_ack:errors',acks.errors)
                app.record_success('poll');poll_failures=0
                if time.time()-cleanup>3600:
                    db.run("DELETE FROM update_inbox WHERE state='done' AND created<?",(time.time()-7*86400,));cleanup=time.time()
            except RuntimeError:raise
            except Exception as error:
                poll_failures+=1
                app.record_failure('poll',error);stop.wait(min(30,2**min(poll_failures-1,5)))
    except KeyboardInterrupt:pass
    finally:
        stop.set();acks.close()
        for worker in workers:worker.join(timeout=7)
        db.conn.close()

if __name__=='__main__':main()
