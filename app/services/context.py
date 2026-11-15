import json
import time
import uuid
from app.utils.core import active_access
from app.services.telegram import APIError
from app.keyboards.inline import keyboard

class Context:
    def __init__(self, db, api, owners, tz='Europe/Paris', support='Administrateur'):
        self.db, self.api, self.owners, self.tz, self.support = db, api, set(owners), tz, support
        self.me = api.call('getMe')
        self.last_tick = 0

    def user(self, uid):
        self.db.run('INSERT INTO users(id) VALUES (?) ON CONFLICT(id) DO NOTHING', (uid,))
        return self.db.one('SELECT * FROM users WHERE id=?', (uid,))

    def group(self, gid):
        return self.db.one('SELECT * FROM groups WHERE id=?', (gid,))

    def deep(self, page='home'):
        return f'https://t.me/{self.me["username"]}?start={page}'

    def admin(self, uid, group=None):
        if uid in self.owners: return True
        if not group or not group['enabled']: return False
        member = self.api.call('getChatMember', chat_id=group['chat'], user_id=uid)
        return member['status'] in ('administrator', 'creator')

    def alert(self, text):
        # Hourly deduplication, failed alerts are queued just like other operations.
        key = 'alert:' + text[:160]
        if float(self.db.setting(key, '0')) > time.time()-3600: return
        self.db.set(key, time.time())
        for uid in self.owners:
            self.enqueue('sendMessage', {'chat_id': uid, 'text': '⚠️ ' + text})

    def enqueue(self, method, payload, due=None):
        self.db.run('INSERT INTO jobs(id,method,payload,due) VALUES (?,?,?,?)',
                    (uuid.uuid4().hex, method, json.dumps(payload), due or time.time()))

    def screen(self, uid, text, rows):
        user = self.user(uid)
        data = dict(chat_id=uid, text=text, parse_mode='HTML', reply_markup=keyboard(*rows),
                    link_preview_options={'is_disabled': True})
        if user['menu']:
            try:
                self.api.call('editMessageText', message_id=user['menu'], **data)
                return
            except APIError as e:
                if 'message is not modified' in str(e): return
                if not any(t in str(e) for t in ('message to edit not found', "message can't be edited")):
                    raise
        result = self.api.call('sendMessage', **data)
        self.db.run('UPDATE users SET menu=? WHERE id=?', (result['message_id'], uid))

    def count(self, uid):
        return self.db.one('SELECT COUNT(*) AS n FROM referrals WHERE inviter=? AND valid=1', (uid,))['n']

    def access(self, uid, gid):
        return self.db.one('SELECT * FROM access WHERE uid=? AND gid=?', (uid, gid))

    def entitled(self, uid, g):
        u = self.user(uid)
        if u['permanent'] or (u['banned'] and g['role'] != 'portal'): return False
        if g['role'] in ('portal', 'free'): return True
        return active_access(self.access(uid, g['id']), time.time())

    def grant(self, uid, g, source, expires=None):
        self.db.run('INSERT INTO access(uid,gid,source,expires) VALUES (?,?,?,?) '
                    'ON CONFLICT(uid,gid) DO UPDATE SET source=excluded.source,expires=excluded.expires,revoked=0',
                    (uid,g['id'],source,expires))

    def link(self, uid, g, purpose='access'):
        row = self.db.one('SELECT url FROM links WHERE uid=? AND gid=? AND purpose=? AND expires>?',
                          (uid,g['id'],purpose,time.time()+60))
        if purpose != 'referral' and self.entitled(uid,g):
            self.api.call('unbanChatMember',chat_id=g['chat'],user_id=uid,only_if_banned=True)
        if row: return row['url']
        expires = int(time.time()+86400 if purpose == 'referral' else time.time()+600)
        data = dict(chat_id=g['chat'], name=f'{purpose[:3]}:{uid}', expire_date=expires)
        if purpose != 'referral': data['creates_join_request'] = True
        result = self.api.call('createChatInviteLink', **data)
        url = result['invite_link']
        self.db.run('INSERT INTO links(url,gid,uid,purpose,expires) VALUES (?,?,?,?,?)',
                    (url,g['id'],uid,purpose,expires))
        return url

