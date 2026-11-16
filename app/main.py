import logging
import os
import time
import threading
from .db import DB, SchemaMismatch
from .services.telegram import Telegram, APIError
from .application import App


def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    token=os.environ['BOT_TOKEN']
    owners={int(x.strip()) for x in os.environ['OWNER_IDS'].split(',') if x.strip()}
    if not owners: raise RuntimeError('OWNER_IDS requis')
    try:
        db=DB(os.environ['DATABASE_URL'])
    except SchemaMismatch as error:
        logging.error('%s',error)
        raise SystemExit(2) from None
    api=Telegram(token)
    app=App(db,api,owners,os.getenv('TIMEZONE','Europe/Paris'),os.getenv('SUPPORT_CONTACT','Administrateur'))
    api.call('deleteWebhook',drop_pending_updates=False)
    app.install_command_menu()
    allowed=['message','edited_message','callback_query','chat_member','my_chat_member','chat_join_request','pre_checkout_query']
    from .services.media import run_worker
    stop=threading.Event()
    worker=threading.Thread(target=run_worker,args=(os.environ['DATABASE_URL'],token,stop),daemon=True)
    worker.start()
    logging.info('Bot démarré. Réception des événements Telegram active ; /start et navigation disponibles.')
    while True:
        try:
            offset=int(db.setting('offset','0'))
            updates=api.call('getUpdates',offset=offset,timeout=10,allowed_updates=allowed)
            db.set('poll:ok',time.time())
            for update in updates:
                try:
                    app.update(update)
                except ValueError as e:
                    msg=update.get('message',{})
                    if msg.get('chat',{}).get('type')=='private':
                        app.screen(msg['from']['id'],str(e),[[{'text':'⬅️ Accueil','callback_data':'home'}]])
                    else: app.alert(str(e))
                except APIError as e:
                    # Keep update for retry on temporary Telegram errors.
                    if e.code==429 or e.code>=500: raise
                    app.alert('Mise à jour à vérifier : '+str(e))
                db.set('offset',update['update_id']+1)
            app.tick()
        except KeyboardInterrupt:
            stop.set()
            worker.join(timeout=5)
            break
        except Exception as e:
            # Do not log raw request URLs: they contain BOT_TOKEN.
            try: app.record_failure('main',e)
            except Exception: logging.error('Cycle interrompu (%s)',type(e).__name__)
            time.sleep(5)

if __name__=='__main__': main()
