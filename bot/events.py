import json
import unicodedata
import uuid
from .domain import now, present, config
from .media import Media

class Events:
    def __init__(self,s):self.s=s;self.media=Media(s)

    def member(self,event):
        s=self.s;db=s.db;chat=event['chat'];new=event['new_chat_member'];u=new['user'];uid=u['id']
        s.upsert_user(u)
        g=db.one('SELECT * FROM groups WHERE chat_id=?',(chat['id'],))
        if uid==s.bot_id:
            if not g:
                if chat['type']!='supergroup':
                    s.alert('type:'+str(chat['id']),'Utiliser un supergroupe sans sujets pour '+chat.get('title','ce groupe'));return
                actor=event.get('from',{}).get('id')
                # Telegram permission check, never infer network ownership from who added the bot.
                adm=s.tg.call('getChatMember',chat_id=chat['id'],user_id=actor)
                if adm['status'] not in ('administrator','creator'):return
                gid=uuid.uuid4().hex[:12]
                db.run('INSERT INTO groups(id,chat_id,name) VALUES(?,?,?)',(gid,chat['id'],chat.get('title','Groupe')))
                g=s.group(gid)
                for owner in s.owners:s.notify(owner,f'➕ {g["name"]} détecté. Ouvre Administration → Groupes pour choisir son rôle.')
            s.health(g);return
        if not g:return
        joined=present(new);was=present(event['old_chat_member'])
        db.run('INSERT INTO membership(gid,uid,present,joined,last_media) VALUES(?,?,?,?,?) ON CONFLICT(gid,uid) DO UPDATE SET present=excluded.present,joined=CASE WHEN membership.present=0 AND excluded.present=1 THEN excluded.joined ELSE membership.joined END,last_media=CASE WHEN membership.present=0 AND excluded.present=1 THEN excluded.last_media ELSE membership.last_media END',(g['id'],uid,int(joined),now(),now()))
        if joined!=was:
            db.run('UPDATE groups SET member_count=CASE WHEN member_count+?>0 THEN member_count+? ELSE 0 END WHERE id=?',(1 if joined else -1,1 if joined else -1,g['id']))
        if not joined:
            # Pending invitations require continuous presence. Validated credit is retained.
            if g['role']=='portal':db.run('UPDATE referrals SET joined=? WHERE invitee=? AND valid=0',(now()+315360000,uid))
            return
        if new['status'] in ('administrator','creator'):return
        ban=s.banned(uid)
        if ban and (g['role']!='portal' or ban['permanent']):
            s.enqueue(f'ban:{g["id"]}:{uid}','ban',{'uid':uid,'gid':g['id']});return
        if g['role']=='portal' and not was and not u.get('is_bot'):
            db.run('UPDATE referrals SET joined=? WHERE invitee=? AND valid=0',(now(),uid))
            url=event.get('invite_link',{}).get('invite_link')
            link=db.one("SELECT * FROM links WHERE url=? AND kind='ref'",(url,))
            if link and link['uid']!=uid:
                db.run('INSERT INTO referrals VALUES(?,?,?,0) ON CONFLICT(invitee) DO NOTHING',(uid,link['uid'],now()))
        elif g['role'] not in ('portal','pending'):
            allow=g['role']=='free' or s.owned(uid,g['id'])
            if g['role']=='proof':
                visit=db.one('SELECT * FROM visits WHERE uid=?',(uid,))
                allow=bool(visit and visit['expires']>now() and visit['gid']==g['id'])
            if not allow:s.enqueue(f'kick:{g["id"]}:{uid}','kick',{'uid':uid,'gid':g['id']})
            elif not was and g['role'] in ('chat','always'):
                s.notify(uid,f'Bienvenue dans {g["name"]} ! Partage des photos ou vidéos pour conserver ton accès.')

    def join_request(self,e):
        s=self.s;db=s.db;uid=e['from']['id'];g=db.one('SELECT * FROM groups WHERE chat_id=?',(e['chat']['id'],))
        if not g:return
        s.upsert_user(e['from'])
        link=db.one("SELECT * FROM links WHERE url=? AND kind='entry'",(e.get('invite_link',{}).get('invite_link'),))
        allowed=bool(link and link['uid']==uid and link['gid']==g['id'] and link['expires']>now() and not s.banned(uid) and g['healthy'])
        if allowed:
            allowed=g['role'] in ('free','proof') or s.owned(uid,g['id'])
        if allowed and g['role']=='proof':
            visit=db.one('SELECT * FROM visits WHERE uid=?',(uid,))
            if visit:allowed=False
            else:
                # Record before approval: a retry cannot provide a second visit.
                db.run('INSERT INTO visits VALUES(?,?,?,?)',(uid,now(),now()+600,g['id']))
                s.enqueue(f'visit:{uid}','kick',{'gid':g['id'],'uid':uid},now()+600)
        s.tg.call('approveChatJoinRequest' if allowed else 'declineChatJoinRequest',chat_id=g['chat_id'],user_id=uid)
        if allowed:
            s.enqueue('revoke:'+str(uid)+':'+g['id'],'revoke',{'gid':g['id'],'url':link['url']})

    def message(self,m):
        s=self.s;db=s.db;g=db.one('SELECT * FROM groups WHERE chat_id=?',(m['chat']['id'],))
        if not g:return
        mid=m['message_id'];uid=m.get('from',{}).get('id',0)
        if m.get('new_chat_members') or m.get('left_chat_member'):
            s.enqueue(f'del:{g["id"]}:{mid}','delete',{'gid':g['id'],'mid':mid});return
        if g['role']=='pending':return
        db.run('INSERT INTO messages VALUES(?,?,?,?,?) ON CONFLICT(gid,mid) DO NOTHING',(g['id'],mid,uid,m.get('media_group_id'),m['date']))
        text=m.get('text',m.get('caption',''))
        admin=s.admin(uid,g['id']) or bool(m.get('sender_chat',{}).get('id')==g['chat_id'])
        if text.split(' ')[0].split('@')[0]=='/pedo' and admin:
            target=m.get('reply_to_message')
            if not target:return
            # Only explicit media targets. Record replied media even when it predates installation.
            if not (target.get('photo') or target.get('video')):return
            target_uid=target.get('from',{}).get('id')
            if not target_uid or s.admin(target_uid,g['id']):return
            f=target.get("video") or target["photo"][-1]
            db.run("INSERT INTO media(gid,mid,uid,file_id,unique_id,kind) VALUES(?,?,?,?,?,?) ON CONFLICT DO NOTHING",(g["id"],target["message_id"],target_uid,f["file_id"],f["file_unique_id"],"video" if target.get("video") else "photo"))
            album=target.get('media_group_id')
            mids=[x['mid'] for x in db.all('SELECT mid FROM messages WHERE gid=? AND album=?',(g['id'],album))] if album else []
            mids=list(set(mids+[target['message_id']]))
            self.media.blacklist(g['id'],mids)
            db.run('INSERT INTO media_queue(id,gid,body) VALUES(?,?,?) ON CONFLICT DO NOTHING',(g['id']+':'+str(target['message_id']),g['id'],json.dumps(target)))
            for x in mids:s.enqueue(f'del:{g["id"]}:{x}','delete',{'gid':g['id'],'mid':x})
            s.ban(target_uid,True,'signalement administrateur de média interdit')
            s.enqueue(f'del:{g["id"]}:{mid}','delete',{'gid':g['id'],'mid':mid});return
        if admin:return
        if s.banned(uid):
            s.enqueue(f'del:{g["id"]}:{mid}','delete',{'gid':g['id'],'mid':mid});return
        c=config(g)
        if g['role'] not in ('portal','free','proof') and not s.owned(uid,g['id']):
            s.enqueue(f'kick:{g["id"]}:{uid}','kick',{'gid':g['id'],'uid':uid});return
        words=db.setting('words',[])
        normalized=unicodedata.normalize('NFKC',text).casefold()
        if any(unicodedata.normalize('NFKC',w).casefold() in normalized for w in words if w):
            fresh=db.run("INSERT INTO sanctions VALUES(?,?,'word') ON CONFLICT DO NOTHING",(g['id'],mid)).rowcount
            if not fresh:return
            db.run('INSERT INTO strikes VALUES(?,1) ON CONFLICT(uid) DO UPDATE SET count=strikes.count+1',(uid,))
            n=db.one('SELECT count FROM strikes WHERE uid=?',(uid,))['count']
            if n>=3:s.ban(uid,reason='mots interdits répétés')
            else:s.enqueue(f'mute:{g["id"]}:{uid}','mute',{'gid':g['id'],'uid':uid,'until':now()+(1 if n==1 else 4)*86400})
            s.enqueue(f'del:{g["id"]}:{mid}','delete',{'gid':g['id'],'mid':mid});return
        if c['reports'] and '@admin' in normalized and m.get('reply_to_message'):
            target=m['reply_to_message'];author=target.get('from',{}).get('id',0)
            if author and author!=uid and not s.admin(author,g['id']):
                key=target.get('media_group_id') or str(target['message_id'])
                db.run('INSERT INTO reports VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING',(g['id'],key,uid,author,now()))
                n=db.one('SELECT COUNT(*) n FROM reports WHERE gid=? AND target=?',(g['id'],key))['n']
                if n>=4:
                    s.ban(author,reason='4 signalements distincts')
                    s.alert('report:'+g['id']+':'+key,f'{g["name"]} : bannissement réversible de {author} après quatre signalements. Vérifier les abus.')
        if g['role'] in ('chat','always','free') and not any(k in m for k in ('text','photo','video')):
            s.enqueue(f'del:{g["id"]}:{mid}','delete',{'gid':g['id'],'mid':mid});return
        if m.get('photo') or m.get('video'):
            db.run('INSERT INTO media_queue(id,gid,body) VALUES(?,?,?) ON CONFLICT DO NOTHING',(g['id']+':'+str(mid),g['id'],json.dumps(m)))
