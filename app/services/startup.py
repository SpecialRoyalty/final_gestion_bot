"""Retry safe bootstrap calls without restarting the container or losing updates."""
import logging
import time
from .telegram import APIError,DeferredRequest,transient_error
from .monitoring import safe_error

class StartupStopped(Exception):pass
class StartupConfigurationError(Exception):pass

def startup_call(api,db,stop,method,**payload):
    failures=0
    while not stop.is_set():
        db.set('startup:phase',method)
        try:
            result=api.call(method,**payload)
            db.set('startup:error','');db.set('startup:retry_at',0)
            return result
        except APIError as error:
            if not transient_error(error):
                # Never include credentials or raw transport URLs in logs.
                message=('Authentification Telegram refusée : vérifier BOT_TOKEN.' if error.code in (401,404) else 'Initialisation Telegram refusée ('+method+', code '+str(error.code)+').')
                db.set('startup:error',message)
                raise StartupConfigurationError(message) from None
            if not isinstance(error,DeferredRequest):failures+=1
            delay=max(float(getattr(error,'retry',0)),min(30,2**min(max(1,failures),5)))
            db.set('startup:error',safe_error(error));db.set('startup:retry_at',time.time()+delay)
            logging.warning('Démarrage en attente : %s (%s), nouvelle tentative dans %.1f s.',method,safe_error(error),delay)
            if stop.wait(delay):break
    raise StartupStopped()
