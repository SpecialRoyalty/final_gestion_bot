import time
from app.utils.core import member_present, forbidden

class Moderation:
    def check_rights(self,g):
        chat=self.api.call('getChat',chat_id=g['chat'])
        if chat['type']!='supergroup': raise ValueError('Utilise un supergroupe Telegram privé.')
        if chat.get('username'): raise ValueError('Le groupe doit être privé pour contrôler les accès.')
        member=self.api.call('getChatMember',chat_id=g['chat'],user_id=self.me['id'])
        required=('can_delete_messages','can_restrict_members','can_invite_users')
        if member['status']!='administrator' or not all(member.get(r) for r in required):
            raise ValueError('Le bot doit être administrateur : supprimer, restreindre, inviter.')

    def ban(self,uid,permanent=False):
        self.user(uid)
        self.db.run('UPDATE users SET banned=1,permanent=CASE WHEN permanent=1 THEN 1 ELSE ? END,ban_base=? WHERE id=?',
                    (int(permanent),self.count(uid),uid))
        for g in self.db.all('SELECT * FROM groups WHERE enabled=1'):
            if g['role']=='portal' and not permanent: continue
            self.enqueue('banChatMember',dict(chat_id=g['chat'],user_id=uid,revoke_messages=True,_if_banned=True))

    def join_request(self,q):
        uid=q['from']['id']; g=self.db.one('SELECT * FROM groups WHERE chat=?',(q['chat']['id'],))
        if not g: return
        link=self.db.one('SELECT * FROM links WHERE url=?',(q.get('invite_link',{}).get('invite_link',''),))
        allowed=bool(g['enabled'] and link and link['uid']==uid and link['expires']>time.time() and self.entitled(uid,g))
        if g['role']=='proof' and not allowed and link and link['uid']==uid and link['expires']>time.time() and g['enabled'] and not self.user(uid)['banned']:
            used=self.db.one('SELECT 1 FROM proof_used WHERE uid=? AND gid=?',(uid,g['id']))
            allowed=not used
            if allowed:
                # Reserve once, then give a temporary entitlement; a retry can approve the same request.
                with self.db.transaction():
                    self.db.run('INSERT INTO proof_used(uid,gid) VALUES (?,?)',(uid,g['id']))
                    self.grant(uid,g,'proof',time.time()+600)
        self.api.call('approveChatJoinRequest' if allowed else 'declineChatJoinRequest',chat_id=g['chat'],user_id=uid)

    def member(self,q):
        g=self.db.one('SELECT * FROM groups WHERE chat=?',(q['chat']['id'],))
        if not g or not g['enabled']: return
        u=q['new_chat_member']['user']; uid=u['id']
        if u.get('is_bot'): return
        present=member_present(q['new_chat_member']); old=member_present(q['old_chat_member'])
        if present==old: return
        self.metric('join' if present else 'leave',uid,g['id'],key=f'member:{g["id"]}:{uid}:{q.get("date",time.time())}:{present}')
        self.user(uid)
        if present:
            self.db.run('INSERT INTO members(uid,gid,joined,media) VALUES (?,?,?,?) ON CONFLICT(uid,gid) DO UPDATE SET present=1,joined=excluded.joined,media=excluded.media,warned=0',
                        (uid,g['id'],time.time(),time.time()))
            if q['new_chat_member']['status'] in ('administrator','creator'): return
            if not self.entitled(uid,g):
                self.enqueue('banChatMember',dict(chat_id=g['chat'],user_id=uid)); return
            if g['role']=='portal':
                link=self.db.one('SELECT * FROM links WHERE url=? AND purpose=?',
                                 (q.get('invite_link',{}).get('invite_link',''),'referral'))
                if link and link['uid']!=uid and link['expires']>time.time():
                    self.db.run('INSERT INTO referrals(invitee,inviter,joined) VALUES (?,?,?) ON CONFLICT(invitee) DO UPDATE SET present=1,joined=excluded.joined',
                                (uid,link['uid'],time.time()))
                else:
                    self.db.run('UPDATE referrals SET present=1,joined=? WHERE invitee=?',(time.time(),uid))
            elif g['role'] in ('chat','always'):
                self.enqueue('sendMessage',dict(chat_id=uid,text='Bienvenue ! Partage régulièrement des photos ou vidéos pour conserver ton accès.'))
        else:
            self.db.run('UPDATE members SET present=0 WHERE uid=? AND gid=?',(uid,g['id']))
            if g['role']=='portal': self.db.run('UPDATE referrals SET present=0 WHERE invitee=?',(uid,))

    def group_message(self,m):
        g=self.db.one('SELECT * FROM groups WHERE chat=?',(m['chat']['id'],))
        if not g or not g['enabled']: return
        uid=m.get('from',{}).get('id',0); mid=m['message_id']
        media=m.get('photo',[{}])[-1] if m.get('photo') else m.get('video',{})
        fid=media.get('file_unique_id')
        previous=self.db.one('SELECT * FROM messages WHERE gid=? AND mid=?',(g['id'],mid))
        self.db.run('INSERT INTO messages(gid,mid,uid,album,file_id,created) VALUES (?,?,?,?,?,?) ON CONFLICT(gid,mid) DO NOTHING',
                    (g['id'],mid,uid,m.get('media_group_id'),fid,m.get('date',time.time())))
        if m.get('new_chat_members') or m.get('left_chat_member'):
            self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=mid)); return
        text=m.get('text',m.get('caption',''))
        is_admin=self.admin(uid,g) if uid else False
        reply=m.get('reply_to_message')
        if text.split(' ')[0].split('@')[0]=='/pedo' and is_admin and reply:
            victim=reply.get('from',{}).get('id')
            if not victim or self.admin(victim,g): return
            self.ban(victim,True)
            album=reply.get('media_group_id')
            items=self.db.all('SELECT * FROM messages WHERE gid=? AND '+('album=?' if album else 'mid=?'),(g['id'],album or reply['message_id']))
            for item in items:
                self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=item['mid']))
                if item['file_id']:
                    self.db.run('INSERT INTO fingerprints(file_id,blocked) VALUES (?,1) ON CONFLICT(file_id) DO UPDATE SET blocked=1',(item['file_id'],))
            self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=mid)); return
        if is_admin: return
        if self.user(uid)['permanent'] or (self.user(uid)['banned'] and g['role']!='portal'):
            self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=mid)); return
        if g['role']=='portal': return
        if forbidden(text,self.db.setting('words').splitlines()):
            row=self.db.one('SELECT count FROM strikes WHERE uid=?',(uid,))
            n=(row['count'] if row else 0)+1
            self.db.run('INSERT INTO strikes(uid,count) VALUES (?,?) ON CONFLICT(uid) DO UPDATE SET count=excluded.count',(uid,n))
            self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=mid))
            if n>=3: self.ban(uid)
            else: self.enqueue('restrictChatMember',dict(chat_id=g['chat'],user_id=uid,permissions={'can_send_messages':False},until_date=int(time.time()+(86400 if n==1 else 4*86400))))
            return
        if reply and '@admin' in text.casefold():
            victim=reply.get('from',{}).get('id')
            if victim and victim!=uid and not self.admin(victim,g):
                target=str(reply.get('media_group_id') or reply['message_id'])
                self.db.run('INSERT INTO reports(gid,target,reporter) VALUES (?,?,?) ON CONFLICT DO NOTHING',(g['id'],target,uid))
                if self.db.one('SELECT COUNT(*) AS n FROM reports WHERE gid=? AND target=?',(g['id'],target))['n']>=4:
                    self.ban(victim)
        if fid and not (previous and previous['file_id']==fid):
            blocked=self.db.one('SELECT 1 FROM fingerprints WHERE file_id=? AND blocked=1',(fid,))
            duplicate=self.db.one('SELECT 1 FROM seen_media WHERE gid=? AND file_id=?',(g['id'],fid))
            if blocked or (g['anti'] and duplicate):
                self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=mid)); return
            self.db.run('INSERT INTO media_tasks(id,gid,mid,file_id,download_id,kind,due) VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                        (g['id']+':'+str(mid),g['id'],mid,fid,media['file_id'],'photo' if m.get('photo') else 'video',time.time()))
            self.db.run('INSERT INTO seen_media(gid,file_id) VALUES (?,?) ON CONFLICT DO NOTHING',(g['id'],fid))
            self.db.run('UPDATE members SET media=?,warned=0 WHERE uid=? AND gid=?',(time.time(),uid,g['id']))
        elif not fid and not m.get('text'):
            self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=mid))

