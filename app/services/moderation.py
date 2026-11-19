import time
import json
from app.utils.core import member_present, forbidden
from app.utils.links import contains_link

class Moderation:
    def check_rights(self,g):
        chat=self.api.call('getChat',chat_id=g['chat'])
        if chat['type']!='supergroup': raise ValueError('Utilise un supergroupe Telegram privé.')
        if chat.get('username'): raise ValueError('Le groupe doit être privé pour contrôler les accès.')
        member=self.api.call('getChatMember',chat_id=g['chat'],user_id=self.me['id'])
        required=('can_delete_messages','can_restrict_members','can_invite_users')
        if member['status']!='administrator' or not all(member.get(r) for r in required):
            raise ValueError('Le bot doit être administrateur : supprimer, restreindre, inviter.')

    def ban(self,uid,permanent=False,include_portal=False):
        self.user(uid)
        if include_portal:self.db.set('link_ban:'+str(uid),'1')
        self.db.run('UPDATE users SET banned=1,permanent=CASE WHEN permanent=1 THEN 1 ELSE ? END,ban_base=? WHERE id=?',
                    (int(permanent),self.count(uid),uid))
        for g in self.db.all('SELECT * FROM groups WHERE enabled=1'):
            if g['role']=='portal' and not permanent and not include_portal: continue
            self.enqueue('banChatMember',dict(chat_id=g['chat'],user_id=uid,revoke_messages=True,_if_banned=True))

    def join_request(self,q):
        uid=q['from']['id']; g=self.db.one('SELECT * FROM groups WHERE chat=?',(q['chat']['id'],))
        if not g:return
        if q['from'].get('is_bot'):
            self.api.call('declineChatJoinRequest',chat_id=g['chat'],user_id=uid);return
        link=self.db.one('SELECT * FROM links WHERE url=?',(q.get('invite_link',{}).get('invite_link',''),))
        allowed=bool(g['enabled'] and link and link['uid']==uid and link['expires']>time.time() and self.entitled(uid,g))
        if g['role']=='free' and link and link['gid']==g['id'] and link['purpose']=='public_free' and link['expires']>time.time():
            allowed=bool(g['enabled'] and self.entitled(uid,g))
        if g['role']=='proof' and not allowed and link and link['uid']==uid and link['expires']>time.time() and g['enabled'] and not self.user(uid)['banned']:
            used=self.db.one('SELECT 1 FROM proof_used WHERE uid=? AND gid=?',(uid,g['id']))
            allowed=not used and self.proof_eligible(uid,g)
            if allowed:
                # Reserve once, then give a temporary entitlement; a retry can approve the same request.
                with self.db.transaction():
                    self.db.run('INSERT INTO proof_used(uid,gid) VALUES (?,?)',(uid,g['id']))
                    self.grant(uid,g,'proof',time.time()+self.proof_minutes(g)*60)
        self.api.call('approveChatJoinRequest' if allowed else 'declineChatJoinRequest',chat_id=g['chat'],user_id=uid)

    def member(self,q):
        g=self.db.one('SELECT * FROM groups WHERE chat=?',(q['chat']['id'],))
        if not g or not g['enabled']: return
        u=q['new_chat_member']['user']; uid=u['id']
        if q['new_chat_member']['status']!=q['old_chat_member']['status'] and any(q[k]['status'] in ('administrator','creator') for k in ('new_chat_member','old_chat_member')):
            key='admin_roster:'+str(g['chat']);cached=json.loads(self.db.setting(key,'{}'))
            ids=set(cached.get('ids',[]));ids.discard(uid)
            if q['new_chat_member']['status'] in ('administrator','creator'):ids.add(uid)
            self.db.set(key,json.dumps({'ids':sorted(ids),'until':0}))
        if u.get('is_bot'):
            if uid!=self.me['id'] and member_present(q['new_chat_member']):
                self.enqueue('banChatMember',dict(chat_id=g['chat'],user_id=uid))
            return
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
                    with self.db.transaction():
                        existing=self.db.one('SELECT invitee FROM referrals WHERE invitee=?',(uid,))
                        self.db.run('INSERT INTO referrals(invitee,inviter,joined) VALUES (?,?,?) ON CONFLICT(invitee) DO UPDATE SET present=1,joined=excluded.joined',
                                    (uid,link['uid'],time.time()))
                        target=self.db.one('SELECT gid FROM target_links WHERE url=?',(link['url'],))
                        if not existing and target:
                            self.db.run('INSERT INTO referral_targets(invitee,gid) VALUES (?,?) ON CONFLICT DO NOTHING',(uid,target['gid']))
                else:
                    self.db.run('UPDATE referrals SET present=1,joined=? WHERE invitee=?',(time.time(),uid))
            elif g['role'] in ('chat','always'):
                self.enqueue('sendMessage',dict(chat_id=uid,text=self.tr(uid,'Bienvenue ! Partage régulièrement des photos ou vidéos pour conserver ton accès.')))
        else:
            self.db.run('UPDATE members SET present=0 WHERE uid=? AND gid=?',(uid,g['id']))
            if g['role']=='portal': self.db.run('UPDATE referrals SET present=0 WHERE invitee=?',(uid,))

    def group_message(self,m):
        g=self.db.one('SELECT * FROM groups WHERE chat=?',(m['chat']['id'],))
        if not g or not g['enabled']: return
        uid=m.get('from',{}).get('id',0); mid=m['message_id']
        if m.get('new_chat_members') or m.get('left_chat_member'):
            self.immediate_delete(g['chat'],mid)
            for added in m.get('new_chat_members',[]):
                if added.get('is_bot') and added['id']!=self.me['id']:self.enqueue('banChatMember',dict(chat_id=g['chat'],user_id=added['id']))
            return
        media=m.get('photo',[{}])[-1] if m.get('photo') else m.get('video',{})
        fid=media.get('file_unique_id')
        previous=self.db.one('SELECT * FROM messages WHERE gid=? AND mid=?',(g['id'],mid))
        self.db.run('INSERT INTO messages(gid,mid,uid,album,file_id,created) VALUES (?,?,?,?,?,?) ON CONFLICT(gid,mid) DO NOTHING',
                    (g['id'],mid,uid,m.get('media_group_id'),fid,m.get('date',time.time())))
        text=m.get('text',m.get('caption',''))
        # Ordinary media ingestion must not wait on Telegram administrator lookups.
        anonymous=m.get('sender_chat',{}).get('id')==g['chat']
        reply=m.get('reply_to_message')
        command=text.strip().casefold().split(maxsplit=1)[0].split('@')[0] if text.strip() else ''
        is_admin=anonymous or uid in self.owners
        sensitive=(command=='/pedo' or contains_link(m) or forbidden(text,self.db.setting('words').splitlines()))
        if uid and not is_admin and (sensitive or self.admin_view(uid,g)):
            is_admin=self.admin(uid,g,fresh=True)
        if command=='/pedo' and is_admin and reply:
            victim=reply.get('from',{}).get('id')
            if not victim or self.admin(victim,g,fresh=True):return
            self.ban(victim,True)
            album=reply.get('media_group_id')
            media_reply=reply.get('photo',[{}])[-1] if reply.get('photo') else reply.get('video',{})
            self.db.run('INSERT INTO messages(gid,mid,uid,album,file_id,created) VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                        (g['id'],reply['message_id'],victim,album,media_reply.get('file_unique_id'),reply.get('date',time.time())))
            items=self.db.all('SELECT * FROM messages WHERE gid=? AND '+('album=?' if album else 'mid=?'),(g['id'],album or reply['message_id']))
            for item in items:
                self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=item['mid']))
                if item['file_id']:
                    self.db.run('INSERT INTO fingerprints(file_id,blocked) VALUES (?,1) ON CONFLICT(file_id) DO UPDATE SET blocked=1',(item['file_id'],))
            self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=mid)); return
        if is_admin or m.get('from',{}).get('id')==self.me['id']: return
        if m.get('sender_chat'):
            # Anonymous administrators are identified by the destination chat itself.
            if m['sender_chat']['id']==g['chat']: return
            if contains_link(m) or self.db.setting('banned_sender:'+str(m['sender_chat']['id'])):
                self.immediate_delete(g['chat'],mid)
                for target in self.db.all('SELECT * FROM groups WHERE enabled=1'):
                    self.enqueue('banChatSenderChat',dict(chat_id=target['chat'],sender_chat_id=m['sender_chat']['id']))
                self.db.set('banned_sender:'+str(m['sender_chat']['id']),'1')
                return
        if uid and contains_link(m):
            self.immediate_delete(g['chat'],mid)
            self.ban(uid,include_portal=True)
            # Enforce locally immediately; the priority jobs lane propagates the network ban.
            try: self.api.call('banChatMember',chat_id=g['chat'],user_id=uid,revoke_messages=True)
            except Exception: pass  # A durable network-ban job already exists.
            return
        account=self.user(uid)
        if account['permanent'] or (account['banned'] and g['role']!='portal'):
            if fid and account['permanent']:
                self.db.run('INSERT INTO fingerprints(file_id,blocked) VALUES (?,1) ON CONFLICT(file_id) DO UPDATE SET blocked=1',(fid,))
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
            if victim and victim!=uid and victim not in self.owners:
                target=str(reply.get('media_group_id') or reply['message_id'])
                self.db.run('INSERT INTO reports(gid,target,reporter) VALUES (?,?,?) ON CONFLICT DO NOTHING',(g['id'],target,uid))
                if self.db.one('SELECT COUNT(*) AS n FROM reports WHERE gid=? AND target=?',(g['id'],target))['n']>=4:
                    if not self.admin(victim,g,fresh=True):self.ban(victim)
        if fid and not (previous and previous['file_id']==fid):
            blocked=self.db.one('SELECT 1 FROM fingerprints WHERE file_id=? AND blocked=1',(fid,))
            duplicate=self.db.one('SELECT 1 FROM seen_media WHERE gid=? AND file_id=?',(g['id'],fid))
            if blocked or (g['anti'] and duplicate):
                if self.admin(uid,g,fresh=True):return
                self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=mid)); return
            self.db.run('INSERT INTO media_tasks(id,gid,mid,file_id,download_id,kind,due) VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                        (g['id']+':'+str(mid),g['id'],mid,fid,media['file_id'],'photo' if m.get('photo') else 'video',time.time()))
            self.db.run('INSERT INTO seen_media(gid,file_id) VALUES (?,?) ON CONFLICT DO NOTHING',(g['id'],fid))
            self.db.run('UPDATE members SET media=?,warned=0 WHERE uid=? AND gid=?',(time.time(),uid,g['id']))
        elif not fid and not m.get('text'):
            self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=mid))

