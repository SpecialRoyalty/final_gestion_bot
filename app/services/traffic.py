"""Shared in-process API admission: reserve capacity for user/moderation actions."""
import threading
import time
import math

class TrafficGate:
    def __init__(self):
        self.lock=threading.Lock();self.active=0;self.background=0
        self.tokens=3.;self.last=time.monotonic();self.cooldowns={};self.network_until=0;self.failures=0;self.link_next={}
    def enter(self,method,chat,background):
        now=time.monotonic()
        with self.lock:
            retry=max(self.cooldowns.get('all',0),self.cooldowns.get(chat,0))-now
            if method=='getUpdates':retry=0  # Polling has a separate Telegram long-poll endpoint.
            if background:retry=max(retry,self.network_until-now)
            self.tokens=min(3.,self.tokens+(now-self.last)*3);self.last=now
            if retry>0:return max(1,math.ceil(retry))
            bulk_link=background and method in ('createChatInviteLink','editChatInviteLink','revokeChatInviteLink')
            if bulk_link and self.link_next.get(chat,0)>now:return self.link_next[chat]-now
            if self.active>=8 or (background and (self.background>=1 or self.tokens<1)):return .1
            if bulk_link:self.link_next[chat]=now+2
            self.active+=1
            if background:self.background+=1;self.tokens-=1
            return 0
    def leave(self,background):
        with self.lock:
            self.active-=1
            if background:self.background-=1
    def limited(self,chat,seconds):
        with self.lock:self.cooldowns[chat if chat is not None else 'all']=max(self.cooldowns.get(chat if chat is not None else 'all',0),time.monotonic()+max(1,seconds))
    def failure(self):
        with self.lock:
            self.failures+=1
            if self.failures>=3:self.network_until=time.monotonic()+min(30,2**min(self.failures-3,5))
    def success(self):
        with self.lock:self.failures=0;self.network_until=0

_GATES={};_LOCK=threading.Lock()
def gate_for(token):
    with _LOCK:
        if token not in _GATES:_GATES[token]=TrafficGate()
        return _GATES[token]
