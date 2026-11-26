import os
from urllib.parse import urlsplit
import json
import time
import threading
import http.client
from .traffic import gate_for

class APIError(Exception):
    def __init__(self,data):
        self.code=data.get('error_code',0);self.retry=data.get('parameters',{}).get('retry_after',0)
        super().__init__(data.get('description','Telegram unavailable'))

class NetworkError(APIError):
    def __init__(self,method,cause=None):
        self.method=method;self.cause=cause
        super().__init__({'error_code':0,'description':'Connexion Telegram interrompue ('+method+'). Résultat non confirmé.'+(' Cause : '+cause+'.' if cause else '')})

class DeferredRequest(APIError):
    def __init__(self,retry=1):
        super().__init__({'error_code':429,'description':'Action différée localement pour préserver les actions prioritaires.','parameters':{'retry_after':retry}})

def blocked_recipient(error):
    return isinstance(error,APIError) and error.code==403 and any(t in str(error).lower() for t in ('bot was blocked by the user','user is deactivated'))

def transient_error(error):
    return isinstance(error,NetworkError) or isinstance(error,APIError) and (error.code==429 or error.code>=500)

def deletion_result(error):
    if not isinstance(error,APIError):return None
    text=str(error).lower()
    if 'message to delete not found' in text:return 'gone'
    if any(t in text for t in ("message can't be deleted",'message cannot be deleted','message can not be deleted')):return 'terminal'
    return None

class Telegram:
    SAFE_PREFIXES=('get','edit','set','delete','revoke','unban','ban','restrict','approve','decline')
    PRIORITY=('answerCallbackQuery','getUpdates','banChatMember','restrictChatMember','approveChatJoinRequest','declineChatJoinRequest','setChatPermissions','getChatMemberCount','deleteMessage','deleteMessages')
    def __init__(self,token,timeout=5,retries=1,background=False):
        self.timeout,self.retries,self.background=timeout,retries,background
        endpoint=os.environ.get('TELEGRAM_API_URL','https://api.telegram.org').rstrip('/')
        parsed=urlsplit(endpoint)
        if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            raise ValueError('TELEGRAM_API_URL doit être une origine HTTP(S), sans chemin ni identifiants.')
        self.endpoint=endpoint;self.host=parsed.netloc;self.scheme=parsed.scheme
        self.custom_endpoint=parsed.hostname!='api.telegram.org'
        self.base=endpoint+'/bot'+token+'/'
        self.path='/bot'+token+'/';self.local=threading.local();self.gate=gate_for(endpoint+token)
    def _close(self):
        conn=getattr(self.local,'connection',None)
        if conn:conn.close()
        self.local.connection=None
    def call(self,method,**data):
        # Background cleanup/bans must not consume the capacity reserved for live moderation.
        background=self.background and (method not in self.PRIORITY or method in ('banChatMember','deleteMessage','deleteMessages'))
        wait=self.gate.enter(method,data.get('chat_id'),background)
        if wait:raise DeferredRequest(wait)
        timeout=data.get('timeout',10)+5 if method=='getUpdates' else (2 if method=='answerCallbackQuery' else self.timeout)
        attempts=self.retries if method.startswith(self.SAFE_PREFIXES) else 1
        reused_read=bool(getattr(self.local,'connection',None)) and method.startswith('get')
        if reused_read:attempts=max(2,attempts)
        try:
            for attempt in range(attempts):
                try:
                    conn=getattr(self.local,'connection',None)
                    if conn is None:
                        factory=http.client.HTTPSConnection if self.scheme=='https' else http.client.HTTPConnection
                        conn=factory(self.host,timeout=timeout);self.local.connection=conn
                    conn.timeout=timeout
                    if conn.sock:conn.sock.settimeout(timeout)
                    conn.request('POST',self.path+method,body=json.dumps(data).encode(),headers={'Content-Type':'application/json','Connection':'keep-alive'})
                    response=conn.getresponse();raw=response.read()
                    try:result=json.loads(raw)
                    except ValueError:result={'ok':False,'error_code':response.status if response.status>=400 else 502,'description':'Réponse Telegram invalide'}
                    if not result.get('ok'):
                        error=APIError(result)
                        if error.code==429:self.gate.limited(data.get('chat_id'),error.retry)
                        if error.code>=500:self.gate.failure()
                        raise error
                    self.gate.success();return result['result']
                except (OSError,http.client.HTTPException) as error:
                    self._close()
                    # Retry only a stale read connection immediately. Never repeat uncertain sends.
                    if attempt==0 and reused_read and isinstance(error,(http.client.RemoteDisconnected,http.client.CannotSendRequest,BrokenPipeError,ConnectionResetError)):
                        continue
                    self.gate.failure()
                    if attempt+1>=self.retries:raise NetworkError(method,type(error).__name__) from None
                    time.sleep(.25*(attempt+1))
        finally:self.gate.leave(background)
