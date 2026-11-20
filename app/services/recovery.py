"""Suspend confirmed inaccessible groups without losing their configuration."""
import time
from .telegram import APIError

class GroupUnavailable(APIError):pass

def kicked(error):
    return isinstance(error,APIError) and error.code==403 and any(s in str(error).lower() for s in ('bot was kicked','bot is not a member'))

class GroupAwareAPI:
    def __init__(self,api,app):self.inner,self.app=api,app
    def __getattr__(self,key):return getattr(self.inner,key)
    def call(self,method,**data):
        try:return self.inner.call(method,**data)
        except APIError as error:
            chat=data.get('chat_id')
            if kicked(error) and isinstance(chat,int) and chat<0:
                if not self.app.suspend_group(chat,'Bot expulsé ou absent du groupe.'):raise
                raise GroupUnavailable({'error_code':403,'description':'Groupe inaccessible : tâches suspendues, configuration conservée.'}) from None
            raise

class Recovery:
    def suspend_group(self,chat,reason):
        g=self.db.one('SELECT * FROM groups WHERE chat=?',(chat,))
        if not g:
            move=self.db.one('SELECT gid FROM portal_moves WHERE old_chat=? ORDER BY created DESC LIMIT 1',(chat,))
            if not move:return False
            self.db.run('INSERT INTO group_pauses(chat,gid,was_enabled,reason,created) VALUES (?,?,0,?,?) ON CONFLICT DO NOTHING',(chat,move['gid'],'Ancien portail inaccessible après remplacement.',time.time()))
            return True
        with self.db.transaction():
            created=self.db.run('INSERT INTO group_pauses(chat,gid,was_enabled,reason,created) VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING',
                                (chat,g['id'],g['enabled'],reason,time.time())).rowcount
            self.db.run('UPDATE groups SET enabled=0 WHERE chat=?',(chat,))
        if created:
            self.alert(g['title']+' : '+reason+' Les tâches de ce groupe sont suspendues. Réajoute le bot avec ses droits, puis active le groupe, ou utilise « Remplacer le groupe Telegram ».')
        for prefix in ('group:','announce:','portal:'):
            self.record_success(prefix+g['id'])
        # Previous global task errors for this exact permanent failure are obsolete.
        for name in ('referrals','rights','rosters'):
            key='task:'+name
            if 'bot was kicked' in self.db.setting(key+':error') or 'bot is not a member' in self.db.setting(key+':error'):self.record_success(key)
        self.db.set('portal_error:'+g['id'],'')
        return True

    def clear_group_pause(self,g):
        self.db.run('DELETE FROM group_pauses WHERE chat=?',(g['chat'],))
        self.db.set('health:'+g['id'],time.time())
