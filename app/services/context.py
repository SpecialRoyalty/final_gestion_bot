import json
import time
import uuid
from app.utils.core import active_access
from app.services.invite_links import PERMANENT_LINK
from app.services.telegram import APIError, blocked_recipient, deletion_result
from app.keyboards.inline import keyboard

class Context:
    def __init__(self, db, api, owners, tz='Europe/Paris', support='Administrateur', me=None):
        self.db, self.api, self.owners, self.tz, self.support = db, api, set(owners), tz, support
        self.me = me or api.call('getMe')
        self.last_tick = 0
        self._fresh_panels=set()
        self.initialize_member_workflows()
        if not self.db.setting('metrics:since'): self.db.set('metrics:since',time.time())

    def install_command_menu(self):
        try:
            self.api.call('setMyCommands',scope={'type':'all_private_chats'},commands=[
                {'command':'start','description':'Ouvrir ou retrouver mon espace'},
                {'command':'support','description':'Contacter le support'}])
            self.api.call('setChatMenuButton',menu_button={'type':'commands'})
            self.db.set('commands:error','')
        except Exception as error:
            self.record_failure('commands',error)

    def user(self, uid):
        existing=self.db.one('SELECT * FROM users WHERE id=?',(uid,))
        if existing:return existing
        self.db.run('INSERT INTO users(id) VALUES (?) ON CONFLICT(id) DO NOTHING', (uid,))
        return self.db.one('SELECT * FROM users WHERE id=?', (uid,))

    def group(self, gid):
        return self.db.one('SELECT * FROM groups WHERE id=?', (gid,))

    def deep(self, page='home'):
        return f'https://t.me/{self.me["username"]}?start={page}'

    def admin(self, uid, group=None, fresh=False):
        if uid in self.owners: return True
        if not group or not group['enabled']: return False
        if fresh:
            member=self.api.call('getChatMember',chat_id=group['chat'],user_id=uid)
            return member['status'] in ('administrator','creator')
        key='admin_roster:'+str(group['chat'])
        cached=json.loads(self.db.setting(key,'{}'))
        if cached.get('until',0)<time.time():
            members=self.api.call('getChatAdministrators',chat_id=group['chat'])
            cached={'until':time.time()+30,'ids':[m['user']['id'] for m in members if m['status'] in ('administrator','creator')]}
            self.db.set(key,json.dumps(cached))
        return uid in cached.get('ids',[])

    def admin_view(self,uid,group):
        """Cached navigation hint only; mutations still verify rights with Telegram."""
        if uid in self.owners:return True
        if not group or not group['enabled']:return False
        cached=json.loads(self.db.setting('admin_roster:'+str(group['chat']),'{}'))
        return uid in cached.get('ids',[])

    def failed_action(self,kind,detail,key=None):
        from app.services.monitoring import safe_error
        self.db.run('INSERT INTO failed_actions(id,kind,detail,created) VALUES (?,?,?,?) ON CONFLICT DO NOTHING',
                    (key or uuid.uuid4().hex,kind,safe_error(detail),time.time()))

    def answer_callback(self,q):
        if q.get('_acknowledged'): return
        try: self.api.call('answerCallbackQuery',callback_query_id=q['id'])
        except APIError as error:
            # The spinner acknowledgement must never cancel the requested action.
            self.db.set('callback_ack:last_error',type(error).__name__)
            self.db.set('callback_ack:error_at',time.time())

    def immediate_delete(self,chat_id,message_id):
        try: self.api.call('deleteMessage',chat_id=chat_id,message_id=message_id)
        except APIError as error:
            result=deletion_result(error)
            if result=='terminal':self.failed_action('deleteMessage',error,f'delete:{chat_id}:{message_id}')
            if not result:
                self.enqueue('deleteMessage',dict(chat_id=chat_id,message_id=message_id))

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

    def refresh_panel(self,uid,render):
        self._fresh_panels.add(uid)
        try: return render()
        finally: self._fresh_panels.discard(uid)

    def language(self,uid):
        from app.utils.i18n import LANGUAGES
        cache=getattr(self,'_render_languages',None)
        if cache is not None and uid in cache:return cache[uid]
        value=self.db.setting('language:'+str(uid),'fr')
        value=value if value in LANGUAGES else 'fr'
        if cache is not None:cache[uid]=value
        return value

    def tr(self,uid,text):
        from app.utils.i18n import translate
        return translate(self.language(uid),text)

    def screen(self, uid, text, rows, photo=None):
        if self.db.setting('dm_blocked:'+str(uid)):return
        try:return self._screen(uid,text,rows,photo)
        except APIError as error:
            if not blocked_recipient(error):raise
            self.db.set('dm_blocked:'+str(uid),time.time())

    def _screen(self, uid, text, rows, photo=None):
        user = self.user(uid)
        data = dict(chat_id=uid, text=text, parse_mode='HTML', reply_markup=keyboard(*rows),
                    link_preview_options={'is_disabled': True})
        old_photo=self.db.setting('menu_photo:'+str(uid))
        if photo:
            data.pop('text'); data.pop('link_preview_options')
            data['caption']=text
        old_menu=user['menu']
        fresh=uid in self._fresh_panels
        if old_menu and not fresh and bool(old_photo)==bool(photo):
            try:
                if photo and old_photo!=photo:
                    self.api.call('editMessageMedia',chat_id=uid,message_id=old_menu,
                                  media={'type':'photo','media':photo,'caption':text,'parse_mode':'HTML'},reply_markup=keyboard(*rows))
                else:
                    self.api.call('editMessageCaption' if photo else 'editMessageText', message_id=old_menu, **data)
                self.db.set('menu_photo:'+str(uid),photo or '')
                return
            except APIError as e:
                if 'message is not modified' in str(e): return
                if not any(t in str(e) for t in ('message to edit not found', "message can't be edited")):
                    raise
        result = self.api.call('sendPhoto',photo=photo,**data) if photo else self.api.call('sendMessage',**data)
        self.db.set('menu_photo:'+str(uid),photo or '')
        self.db.run('UPDATE users SET menu=? WHERE id=?', (result['message_id'], uid))
        if old_menu and old_menu!=result['message_id']:
            try:
                self.api.call('deleteMessage',chat_id=uid,message_id=old_menu)
            except Exception as error:
                if blocked_recipient(error):
                    self.db.set('dm_blocked:'+str(uid),time.time());return
                result=deletion_result(error)
                if result=='gone':return
                if result=='terminal':self.failed_action('panel_cleanup',error,f'delete:{uid}:{old_menu}')
                # Keep a working new panel and retire obsolete buttons.
                if not result:self.enqueue('deleteMessage',dict(chat_id=uid,message_id=old_menu),due=time.time()+5)
                self.enqueue('editMessageReplyMarkup',dict(chat_id=uid,message_id=old_menu,reply_markup={'inline_keyboard':[]}))
                if not result:self.record_failure('panel_cleanup',error)

    def count(self, uid, gid=None):
        if gid is None:
            real=self.db.one('SELECT COUNT(*) AS n FROM referrals WHERE inviter=? AND valid=1',(uid,))['n']
            bonus=self.db.one("SELECT COUNT(*) AS n FROM bug_tickets WHERE uid=? AND status='approved'",(uid,))['n']
        else:
            real=self.db.one('SELECT COUNT(*) AS n FROM referrals r JOIN referral_targets t ON t.invitee=r.invitee WHERE r.inviter=? AND r.valid=1 AND t.gid=?',(uid,gid))['n']
            bonus=self.db.one("SELECT COUNT(*) AS n FROM bug_tickets WHERE uid=? AND gid=? AND status='approved'",(uid,gid))['n']
        return real+5*bonus

    def access(self, uid, gid):
        return self.db.one('SELECT * FROM access WHERE uid=? AND gid=?', (uid, gid))

    def entitled(self, uid, g):
        u = self.user(uid)
        if u['permanent'] or (u['banned'] and (g['role'] != 'portal' or self.db.setting('link_ban:'+str(uid)))): return False
        if g['role'] in ('portal', 'free'): return True
        return active_access(self.access(uid, g['id']), time.time())

    def grant(self, uid, g, source, expires=None):
        previous=self.access(uid,g['id'])
        self.db.run('INSERT INTO access(uid,gid,source,expires) VALUES (?,?,?,?) '
                    'ON CONFLICT(uid,gid) DO UPDATE SET source=excluded.source,expires=excluded.expires,revoked=0',
                    (uid,g['id'],source,expires))
        if not active_access(previous,time.time()) or previous['expires']!=expires:
            self.metric('grant',uid,g['id'])

    def link(self, uid, g, purpose='access'):
        row = self.db.one('SELECT * FROM links WHERE uid=? AND gid=? AND purpose=? AND (expires=-1 OR expires>?) ORDER BY CASE WHEN expires=-1 THEN 0 ELSE 1 END,expires DESC LIMIT 1',
                          (uid,g['id'],purpose,time.time()+60))
        if row:return self.make_link_permanent(row,g) if purpose=='referral' else row['url']
        if purpose != 'referral' and self.entitled(uid,g):
            self.api.call('unbanChatMember',chat_id=g['chat'],user_id=uid,only_if_banned=True)
        expires=PERMANENT_LINK if purpose=='referral' else int(time.time()+600)
        data=dict(chat_id=g['chat'],name=f'{purpose[:3]}:{uid}')
        if purpose!='referral':data.update(creates_join_request=True,expire_date=expires)
        result = self.api.call('createChatInviteLink', **data)
        url = result['invite_link']
        self.db.run('INSERT INTO links(url,gid,uid,purpose,expires) VALUES (?,?,?,?,?)',
                    (url,g['id'],uid,purpose,expires))
        return url

