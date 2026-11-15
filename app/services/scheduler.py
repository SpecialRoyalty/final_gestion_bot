import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from app.utils.core import progress, member_present, window, countdown
from app.services.telegram import APIError
from app.keyboards.inline import button, keyboard

class Scheduler:
    def permissions(self,g,opened):
        self.api.call('setChatPermissions',chat_id=g['chat'],use_independent_chat_permissions=True,
            permissions={'can_send_messages':opened,'can_send_photos':opened,'can_send_videos':opened,
                         'can_send_audios':False,'can_send_documents':False,'can_send_voice_notes':False,
                         'can_send_video_notes':False,'can_send_polls':False,'can_send_other_messages':False,
                         'can_add_web_page_previews':False,'can_invite_users':False,
                         'can_change_info':False,'can_pin_messages':False})

    def notice(self,g,kind,text,rows):
        old=self.db.one('SELECT * FROM notices WHERE gid=? AND kind=?',(g['id'],kind))
        body=json.dumps([text,rows],ensure_ascii=False)
        if old and old['body']==body: return
        args=dict(chat_id=g['chat'],text=text,reply_markup=keyboard(*rows))
        if old:
            try:
                self.api.call('editMessageText',message_id=old['mid'],**args)
                self.db.run('UPDATE notices SET body=? WHERE gid=? AND kind=?',(body,g['id'],kind)); return
            except APIError as e:
                if 'message is not modified' in str(e): return
                if 'message to edit not found' not in str(e): raise
        sent=self.api.call('sendMessage',**args)
        self.db.run('INSERT INTO notices(gid,kind,mid,body) VALUES (?,?,?,?) ON CONFLICT(gid,kind) DO UPDATE SET mid=excluded.mid,body=excluded.body',
                    (g['id'],kind,sent['message_id'],body))

    def remove_notice(self,g,kind):
        old=self.db.one('SELECT * FROM notices WHERE gid=? AND kind=?',(g['id'],kind))
        if old:
            self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=old['mid']))
            self.db.run('DELETE FROM notices WHERE gid=? AND kind=?',(g['id'],kind))

    def jobs(self):
        for job in self.db.all('SELECT * FROM jobs WHERE due<=? ORDER BY due LIMIT 30',(time.time(),)):
            try:
                payload=json.loads(job['payload'])
                guard_gid=payload.pop('_unless_entitled',None)
                guard_ban=payload.pop('_if_banned',False)
                guard_unban=payload.pop('_unless_banned',False)
                skip=(guard_gid and self.entitled(payload['user_id'],self.group(guard_gid)))
                if guard_ban: skip=not self.user(payload['user_id'])['banned']
                if guard_unban: skip=bool(self.user(payload['user_id'])['banned'])
                if not skip: self.api.call(job['method'],**payload)
                self.db.run('DELETE FROM jobs WHERE id=?',(job['id'],))
            except APIError as e:
                # A missing message is already deleted. Other failures remain visible/retriable.
                if job['method']=='deleteMessage' and 'message to delete not found' in str(e):
                    self.db.run('DELETE FROM jobs WHERE id=?',(job['id'],)); continue
                attempts=job['attempts']+1
                delay=max(e.retry,min(3600,2**min(attempts,12)))
                self.db.run('UPDATE jobs SET attempts=?,due=? WHERE id=?',(attempts,time.time()+delay,job['id']))
                if attempts==3 and job['method']!='sendMessage': self.alert('Opération à vérifier : '+job['method']+' — '+str(e))

    def close(self,g):
        # Lock first, enqueue all cleanup transactionally, then record closure.
        self.permissions(g,False)
        with self.db.transaction():
            items=self.db.all('SELECT mid FROM messages WHERE gid=?',(g['id'],))
            items += self.db.all('SELECT mid FROM notices WHERE gid=?',(g['id'],))
            mids=sorted({r['mid'] for r in items})
            for start in range(0,len(mids),100):
                self.enqueue('deleteMessages',dict(chat_id=g['chat'],message_ids=mids[start:start+100]))
            self.db.run('DELETE FROM messages WHERE gid=?',(g['id'],))
            self.db.run('DELETE FROM notices WHERE gid=?',(g['id'],))
            self.db.run('UPDATE groups SET opened=0 WHERE id=?',(g['id'],))

    def scheduled(self,groups,now):
        local=datetime.fromtimestamp(now,ZoneInfo(self.tz))
        candidates=[g for g in groups if g['role']=='chat']
        windows={}
        for day in (local.date()-timedelta(days=1),local.date(),local.date()+timedelta(days=1)):
            key=day.isoformat()
            session=self.db.one('SELECT * FROM sessions WHERE day=?',(key,))
            if not session and candidates and day>=local.date():
                previous=self.db.one('SELECT gid FROM sessions WHERE day<? ORDER BY day DESC LIMIT 1',(key,))
                pool=[g for g in candidates if not previous or g['id']!=previous['gid']] or candidates
                selected=min(pool,key=lambda g:(g['last_open'],g['id']))
                self.db.run('INSERT INTO sessions(day,gid) VALUES (?,?) ON CONFLICT DO NOTHING',(key,selected['id']))
                session={'gid':selected['id']}
            if session:
                selected=next((g for g in candidates if g['id']==session['gid']),None)
                if selected:
                    a,b=window(day,selected['slot'],self.tz)
                    if b>now: windows.setdefault(selected['id'],[]).append((a,b))
        for g in groups:
            if g['role']=='free':
                for delta in range(-1,8):
                    day=local.date()+timedelta(days=delta)
                    if day.weekday()==g['weekday']:
                        a,b=window(day,g['slot'],self.tz)
                        if b>now: windows.setdefault(g['id'],[]).append((a,b))
        return windows

    def tick(self):
        now=time.time()
        self.jobs()
        if now-self.last_tick<20: return
        self.last_tick=now
        warning=self.db.setting('media_warning')
        if warning:
            self.alert(warning); self.db.set('media_warning','')
        # Confirm current membership instead of relying solely on possibly missed leave updates.
        portal=self.db.one("SELECT * FROM groups WHERE role='portal' AND enabled=1")
        if portal:
            for r in self.db.all('SELECT * FROM referrals WHERE valid=0 AND present=1 AND joined<=? LIMIT 100',(now-300,)):
                member=self.api.call('getChatMember',chat_id=portal['chat'],user_id=r['invitee'])
                if not member_present(member):
                    self.db.run('UPDATE referrals SET present=0 WHERE invitee=?',(r['invitee'],)); continue
                if self.db.setting('referral_rule','simple')=='cascade':
                    # Child must have stayed 5 min, not be recursively validated (avoids deadlock).
                    children=self.db.all('SELECT * FROM referrals WHERE inviter=? AND present=1 AND joined<=?',(r['invitee'],now-300))
                    qualifies=False
                    for child in children:
                        if member_present(self.api.call('getChatMember',chat_id=portal['chat'],user_id=child['invitee'])):
                            qualifies=True; break
                    if not qualifies: continue
                self.db.run('UPDATE referrals SET valid=1 WHERE invitee=?',(r['invitee'],))
        for a in self.db.all('SELECT * FROM access WHERE revoked=0 AND expires IS NOT NULL AND expires<=?',(now,)):
            g=self.group(a['gid'])
            if g:
                with self.db.transaction():
                    self.enqueue('banChatMember',dict(chat_id=g['chat'],user_id=a['uid'],_unless_entitled=g['id']))
                    self.db.run('UPDATE access SET revoked=1 WHERE uid=? AND gid=?',(a['uid'],a['gid']))
        self.db.run("UPDATE orders SET status='expired' WHERE (status='pending' AND created<?) OR (status='checkout' AND created<?)",(now-3600,now-900))
        groups=self.db.all('SELECT * FROM groups WHERE enabled=1')
        windows=self.scheduled(groups,now)
        # Close all outdated sessions BEFORE opening another, even across midnight.
        for g in groups:
            spans=windows.get(g['id'],[])
            should_open=g['role'] in ('always','vip','proof') or any(a<=now<b for a,b in spans)
            if g['role']=='portal': continue
            if g['opened'] and not should_open:
                self.close(g); g['opened']=0
        for g in groups:
            try:
                spans=windows.get(g['id'],[])
                active=next(((a,b) for a,b in spans if a<=now<b),None)
                should_open=g['role'] in ('always','vip','proof') or bool(active)
                count=None
                if should_open and not g['opened'] and g['locker']:
                    # Telegram authoritative count, minus bot. No claim that members are active.
                    count=max(0,self.api.call('getChatMemberCount',chat_id=g['chat'])-1)
                    should_open=count>=g['threshold']
                if g['role']!='portal':
                    if should_open and not g['opened']:
                        # Admin permissions are unaffected by setChatPermissions.
                        self.permissions(g,True)
                        self.db.run('UPDATE groups SET opened=1,last_open=? WHERE id=?',(now,g['id']))
                        g['opened']=1
                    elif not g['opened'] and not self.db.setting('locked:'+g['id']):
                        self.permissions(g,False); self.db.set('locked:'+g['id'],'1')
                    invite=[button('🔗 J’invite',url=self.deep('invite'))]
                    if count is not None and count<g['threshold']:
                        self.notice(g,'status',f'🔒 Le groupe démarrera avec au moins {g["threshold"]} membres présents, pendant son créneau.\n\n'+progress(count,g['threshold']),[invite])
                    elif g['opened'] and active:
                        remaining=active[1]-now
                        message='🟢 Groupe ouvert, vous pouvez envoyer textes, photos et vidéos.'
                        if remaining<=1800: message+='\nFermeture dans '+countdown(remaining)+'.'
                        self.notice(g,'status',message,[invite])
                    elif not g['opened']:
                        future=[a for a,b in spans if a>now]
                        message='🔴 Groupe fermé.'+(' Ouverture dans '+countdown(min(future)-now)+'.' if future else ' Prochaine ouverture annoncée ici.')
                        self.notice(g,'status',message,[invite])
                    if g['opened'] and g['rules']:
                        bucket=str(int(now//1800))
                        if self.db.setting('rules:'+g['id'])!=bucket:
                            self.remove_notice(g,'rules')
                            self.notice(g,'rules',g['rules'],[])
                            self.db.set('rules:'+g['id'],bucket)
                if now-float(self.db.setting('health:'+g['id'],'0'))>=7200:
                    self.check_rights(g)
                    self.db.set('health:'+g['id'],now)
            except (APIError,ValueError) as e:
                self.alert(g['title']+' : '+str(e))
        # Public portal & temporary cross-promotion, maintained in one message per group.
        live=self.db.one("SELECT * FROM groups WHERE enabled=1 AND role='chat' AND opened=1")
        for g in groups:
            if g['role']=='portal':
                text=g['description'] or '✨ Bienvenue ! Découvre les groupes et débloque tes accès.'
                if live: text+='\n\n🔥 '+live['title']+' est ouvert !'
                rows=[[button('👤 Découvrir les groupes',url=self.deep())]]
                if live: rows.insert(0,[button('🔥 '+live['title'],url=self.deep('g_'+live['id']))])
                self.notice(g,'portal',text,rows)
            elif g['role'] in ('chat','always','free'):
                if live and g['id']!=live['id']:
                    self.notice(g,'live','🔥 '+live['title']+' est ouvert !',
                        [[button('👀 Découvrir',url=self.deep('g_'+live['id']))],[button('🔗 J’invite',url=self.deep('invite'))]])
                else: self.remove_notice(g,'live')
        for m in self.db.all("SELECT m.* FROM members m JOIN groups g ON m.gid=g.id WHERE m.present=1 AND g.enabled=1 AND g.role IN ('chat','always') AND m.media<=?",(now-3*86400,)):
            g=self.group(m['gid'])
            if self.admin(m['uid'],g): continue
            if m['media']<=now-4*86400:
                with self.db.transaction():
                    self.enqueue('banChatMember',dict(chat_id=g['chat'],user_id=m['uid'],_unless_entitled=g['id']))
                    self.db.run('UPDATE members SET present=0 WHERE uid=? AND gid=?',(m['uid'],g['id']))
                    self.db.run('UPDATE access SET revoked=1,base=? WHERE uid=? AND gid=?',(self.count(m['uid']),m['uid'],g['id']))
            elif not m['warned']:
                self.enqueue('sendMessage',dict(chat_id=m['uid'],text='⚠️ Partage un média dans '+g['title']+' avant demain pour conserver ton accès.'))
                self.db.run('UPDATE members SET warned=1 WHERE uid=? AND gid=?',(m['uid'],g['id']))
        advertised=set()
        for ad in self.db.all('SELECT a.* FROM ads a JOIN groups g ON a.gid=g.id WHERE a.enabled=1 AND g.enabled=1 AND g.opened=1 AND a.next_at<=? ORDER BY a.next_at,a.id',(now,)):
            if ad['gid'] in advertised: continue
            advertised.add(ad['gid'])
            g=self.group(ad['gid']); self.remove_notice(g,'ad')
            if ad['photo']:
                sent=self.api.call('sendPhoto',chat_id=g['chat'],photo=ad['photo'],caption=ad['text'])
            else: sent=self.api.call('sendMessage',chat_id=g['chat'],text=ad['text'])
            self.db.run("INSERT INTO notices(gid,kind,mid,body) VALUES (?,'ad',?,?) ON CONFLICT(gid,kind) DO UPDATE SET mid=excluded.mid,body=excluded.body",(g['id'],sent['message_id'],ad['text']))
            # Rotate campaigns within the group, one visible ad at a time.
            self.db.run('UPDATE ads SET next_at=? WHERE gid=?',(now+ad['interval_hours']*3600,g['id']))
            self.db.run('UPDATE ads SET next_at=? WHERE id=?',(now+(ad['interval_hours']*3600)+1,ad['id']))
        self.jobs()

