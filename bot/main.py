import logging
import os
import time
import threading
from .db import DB
from .telegram import Telegram, APIError
from .app import App


def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    token=os.environ['BOT_TOKEN']
    owners={int(x.strip()) for x in os.environ['OWNER_IDS'].split(',') if x.strip()}
    if not owners: raise RuntimeError('OWNER_IDS requis')
    db=DB(os.environ['DATABASE_URL'])
    api=Telegram(token)
    app=App(db,api,owners,os.getenv('TIMEZONE','Europe/Paris'),os.getenv('SUPPORT_CONTACT','Administrateur'))
    api.call('deleteWebhook',drop_pending_updates=False)
    allowed=['message','edited_message','callback_query','chat_member','my_chat_member','chat_join_request','pre_checkout_query']
    from .media import run_worker
    stop=threading.Event()
    worker=threading.Thread(target=run_worker,args=(os.environ['DATABASE_URL'],token,stop),daemon=True)
    worker.start()
    logging.info('Bot démarré. Un seul processus de polling autorisé.')
    while True:
        try:
            app.tick()
            offset=int(db.setting('offset','0'))
            updates=api.call('getUpdates',offset=offset,timeout=10,allowed_updates=allowed)
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
        except KeyboardInterrupt:
            stop.set()
            worker.join(timeout=5)
            break
        except Exception as e:
            # Do not log raw request URLs: they contain BOT_TOKEN.
            logging.error('Cycle interrompu (%s), nouvelle tentative dans 5 secondes',type(e).__name__)
            time.sleep(5)

if __name__=='__main__': main()
