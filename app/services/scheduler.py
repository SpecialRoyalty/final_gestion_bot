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

    def notice(self,g,kind,text,rows,force=False,photo=None):
        old=self.db.one('SELECT * FROM notices WHERE gid=? AND kind=?',(g['id'],kind))
        body=json.dumps([text,rows,photo],ensure_ascii=False)
        prefix='notice:'+g['id']+':'+kind
        verified=float(self.db.setting(prefix+':verified','0'))
        if old and old['body']==body and not force:
            if time.time()-verified<60: return
            try:
                # A same-keyboard edit probes existence without changing text or posting again.
                self.api.call('editMessageReplyMarkup',chat_id=g['chat'],message_id=old['mid'],reply_markup=keyboard(*rows))
            except APIError as error:
                if 'message is not modified' in str(error): pass
                elif 'message to edit not found' in str(error):
                    self.db.run('DELETE FROM notices WHERE gid=? AND kind=?',(g['id'],kind))
                    old=None
                else: raise
            self.db.set(prefix+':verified',time.time())
            if old: return
        if old and kind=='status':
            try:
                self.api.call('deleteMessage',chat_id=g['chat'],message_id=old['mid'])
            except APIError as error:
                if 'message to delete not found' not in str(error): raise
            self.db.run('DELETE FROM notices WHERE gid=? AND kind=?',(g['id'],kind))
            old=None
        if photo and len(text.encode('utf-16-le'))//2>1024:
            raise ValueError('Texte du portail trop long pour accompagner une image ; raccourcis-le.')
        args=dict(chat_id=g['chat'],reply_markup=keyboard(*rows))
        if old:
            oldbody=json.loads(old['body']); oldphoto=oldbody[2] if len(oldbody)>2 else None
            try:
                if photo and photo!=oldphoto:
                    self.api.call('editMessageMedia',message_id=old['mid'],media={'type':'photo','media':photo,'caption':text},**args)
                elif photo:
                    self.api.call('editMessageCaption',message_id=old['mid'],caption=text,**args)
                else:
                    self.api.call('editMessageText',message_id=old['mid'],text=text,**args)
                self.db.run('UPDATE notices SET body=? WHERE gid=? AND kind=?',(body,g['id'],kind))
                self.db.set(prefix+':verified',time.time()); self.db.set(prefix+':changed',time.time())
                return
            except APIError as error:
                if 'message is not modified' in str(error):
                    self.db.run('UPDATE notices SET body=? WHERE gid=? AND kind=?',(body,g['id'],kind))
                    self.db.set(prefix+':verified',time.time()); return
                if 'message to edit not found' not in str(error): raise
        if photo: sent=self.api.call('sendPhoto',photo=photo,caption=text,**args)
        else: sent=self.api.call('sendMessage',text=text,**args)
        self.db.run('INSERT INTO notices(gid,kind,mid,body) VALUES (?,?,?,?) ON CONFLICT(gid,kind) DO UPDATE SET mid=excluded.mid,body=excluded.body',
                    (g['id'],kind,sent['message_id'],body))
        self.db.set(prefix+':verified',time.time()); self.db.set(prefix+':changed',time.time())

    def publish_portal(self,g,force=False):
        if g['role']!='portal' or not g['enabled']: return False
        live=self.db.one("SELECT * FROM groups WHERE enabled=1 AND role='chat' AND opened=1")
        text=g['description'] or '✨ Bienvenue ! Découvre les groupes et débloque tes accès.'
        if live: text+='\n\n🔥 '+live['title']+' est ouvert !'
        rows=[[button('👤 Découvrir les groupes',url=self.deep())]]
        if live: rows.insert(0,[button('🔥 '+live['title'],url=self.deep('g_'+live['id']))])
        try:
            self.notice(g,'portal',text,rows,force=force,photo=self.db.setting('portal_photo:'+g['id']) or None)
        except Exception as error:
            from app.services.monitoring import safe_error
            message='Publication impossible : '+safe_error(error)
            self.db.set('portal_error:'+g['id'],message)
            self.alert(g['title']+' : '+message)
            return False
        self.db.set('portal_error:'+g['id'],'')
        return True

    def remove_notice(self,g,kind):
        old=self.db.one('SELECT * FROM notices WHERE gid=? AND kind=?',(g['id'],kind))
        if old:
            self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=old['mid']))
            self.db.run('DELETE FROM notices WHERE gid=? AND kind=?',(g['id'],kind))

    def jobs(self):
        deadline=time.monotonic()+10
        for job in self.db.all('SELECT * FROM jobs WHERE due<=? ORDER BY due LIMIT 5',(time.time(),)):
            if time.monotonic()>deadline: break
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
            except Exception as e:
                # A missing message is already deleted. Other failures remain visible/retriable.
                if job['method']=='deleteMessage' and 'message to delete not found' in str(e):
                    self.db.run('DELETE FROM jobs WHERE id=?',(job['id'],)); continue
                attempts=job['attempts']+1
                delay=max(getattr(e,'retry',0),min(3600,2**min(attempts,12)))
                self.db.run('UPDATE jobs SET attempts=?,due=? WHERE id=?',(attempts,time.time()+delay,job['id']))
                if attempts==3 and job['method']!='sendMessage': self.record_failure('job:'+job['method'],e)

    def close(self,g):
        # Lock first, enqueue all cleanup transactionally, then record closure.
        self.permissions(g,False)
        with self.db.transaction():
            items=self.db.all('SELECT mid FROM messages WHERE gid=?',(g['id'],))
            items += self.db.all("SELECT mid FROM notices WHERE gid=? AND kind!='status'",(g['id'],))
            mids=sorted({r['mid'] for r in items})
            for start in range(0,len(mids),100):
                self.enqueue('deleteMessages',dict(chat_id=g['chat'],message_ids=mids[start:start+100]))
            self.db.run('DELETE FROM messages WHERE gid=?',(g['id'],))
            self.db.run("DELETE FROM notices WHERE gid=? AND kind!='status'",(g['id'],))
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

    def task_referrals(self,now):
        # Confirm current membership instead of relying solely on possibly missed leave updates.
        portal=self.db.one("SELECT * FROM groups WHERE role='portal' AND enabled=1")
        errors=[]
        if portal:
            cursor=int(self.db.setting('referrals:cursor','0'))
            rows=self.db.all('SELECT * FROM referrals WHERE valid=0 AND present=1 AND joined<=? AND invitee>? ORDER BY invitee LIMIT 10',(now-300,cursor))
            if not rows:
                rows=self.db.all('SELECT * FROM referrals WHERE valid=0 AND present=1 AND joined<=? ORDER BY invitee LIMIT 10',(now-300,))
            deadline=time.monotonic()+15
            for r in rows:
                if time.monotonic()>deadline: break
                self.db.set('referrals:cursor',r['invitee'])
                try:
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
                    with self.db.transaction():
                        self.db.run('UPDATE referrals SET valid=1 WHERE invitee=?',(r['invitee'],))
                        target=self.db.one('SELECT gid FROM referral_targets WHERE invitee=?',(r['invitee'],))
                        self.metric('referral_valid',r['inviter'],target['gid'] if target else portal['id'],key='valid:'+str(r['invitee']))
                except Exception as error:
                    errors.append(type(error).__name__)
        if errors: raise RuntimeError(f'{len(errors)} invitations non vérifiées : '+', '.join(sorted(set(errors))))

    def task_expiry(self,now):
        for a in self.db.all('SELECT * FROM access WHERE revoked=0 AND expires IS NOT NULL AND expires<=?',(now,)):
            g=self.group(a['gid'])
            if g:
                with self.db.transaction():
                    self.enqueue('banChatMember',dict(chat_id=g['chat'],user_id=a['uid'],_unless_entitled=g['id']))
                    self.db.run('UPDATE access SET revoked=1 WHERE uid=? AND gid=?',(a['uid'],a['gid']))
        self.db.run("UPDATE orders SET status='expired' WHERE (status='pending' AND created<?) OR (status='checkout' AND created<?)",(now-3600,now-900))

    def task_schedule(self,now):
        groups=self.db.all('SELECT * FROM groups WHERE enabled=1')
        windows=self.scheduled(groups,now)
        # Close all outdated sessions BEFORE opening another, even across midnight.
        failed_closures=set()
        for g in groups:
            spans=windows.get(g['id'],[])
            should_open=g['role'] in ('always','vip','proof') or any(a<=now<b for a,b in spans)
            if g['role']=='portal': continue
            if g['opened'] and not should_open:
                try:
                    self.close(g); g['opened']=0
                except Exception as error:
                    failed_closures.add(g['id'])
                    self.record_failure('group:'+g['id'],error)
        for g in groups:
            if g['id'] in failed_closures: continue
            try:
                self.db.set('group:'+g['id']+':attempt',now)
                spans=windows.get(g['id'],[])
                active=next(((a,b) for a,b in spans if a<=now<b),None)
                should_open=g['role'] in ('always','vip','proof') or bool(active)
                count=None
                if should_open and not g['opened'] and g['locker']:
                    # Telegram authoritative count, minus bot. No claim that members are active.
                    count=max(0,self.api.call('getChatMemberCount',chat_id=g['chat'])-1)
                    should_open=count>=g['threshold']
                if g['role']!='portal':
                    if should_open and not g['opened'] and g['role']=='chat':
                        other=self.db.one("SELECT id FROM groups WHERE role='chat' AND opened=1 AND id!=?",(g['id'],))
                        if other: raise ValueError('Ouverture bloquée : la fermeture du groupe précédent doit être confirmée.')
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
                        if remaining<=1800: message+='\nFermeture dans '+countdown(remaining,closing=True)+'.'
                        message+='\nHeure de fermeture : '+datetime.fromtimestamp(active[1],ZoneInfo(self.tz)).strftime('%H:%M')+' ('+self.tz+').'
                        self.notice(g,'status',message,[invite])
                    elif not g['opened']:
                        future=[a for a,b in spans if a>now]
                        message='🔴 Groupe fermé.'+(' Ouverture dans '+countdown(min(future)-now,weekly=g['role']=='free')+'.' if future else ' Prochaine ouverture annoncée ici.')
                        if future: message+='\nPrévue le '+datetime.fromtimestamp(min(future),ZoneInfo(self.tz)).strftime('%d/%m à %H:%M')+' ('+self.tz+').'
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
                self.db.set('group:'+g['id']+':ok',time.time())
                self.db.set('group:'+g['id']+':error','')
            except Exception as error:
                self.record_failure('group:'+g['id'],error)

    def task_announcements(self,now):
        groups=self.db.all('SELECT * FROM groups WHERE enabled=1')
        # Public portal & temporary cross-promotion, maintained in one message per group.
        live=self.db.one("SELECT * FROM groups WHERE enabled=1 AND role='chat' AND opened=1")
        for g in groups:
            try:
                if g['role']=='portal':
                    self.publish_portal(g)
                elif g['role'] in ('chat','always','free'):
                    if live and g['id']!=live['id']:
                        self.notice(g,'live','🔥 '+live['title']+' est ouvert !',
                            [[button('👀 Découvrir',url=self.deep('g_'+live['id']))],[button('🔗 J’invite',url=self.deep('invite'))]])
                    else: self.remove_notice(g,'live')
            except Exception as error:
                self.record_failure('announce:'+g['id'],error)

    def task_activity(self,now):
        for m in self.db.all("SELECT m.* FROM members m JOIN groups g ON m.gid=g.id WHERE m.present=1 AND g.enabled=1 AND g.role IN ('chat','always') AND m.media<=?",(now-3*86400,)):
            g=self.group(m['gid'])
            if self.admin(m['uid'],g): continue
            if m['media']<=now-4*86400:
                with self.db.transaction():
                    self.enqueue('banChatMember',dict(chat_id=g['chat'],user_id=m['uid'],_unless_entitled=g['id']))
                    self.db.run('UPDATE members SET present=0 WHERE uid=? AND gid=?',(m['uid'],g['id']))
                    self.db.run('UPDATE access SET revoked=1,base=? WHERE uid=? AND gid=?',(self.count(m['uid'],g['id']),m['uid'],g['id']))
            elif not m['warned']:
                self.enqueue('sendMessage',dict(chat_id=m['uid'],text=self.tr(m['uid'],'⚠️ Partage un média dans ')+g['title']+self.tr(m['uid'],' avant demain pour conserver ton accès.')))
                self.db.run('UPDATE members SET warned=1 WHERE uid=? AND gid=?',(m['uid'],g['id']))

    def task_ads(self,now):
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

    def tick(self):
        now=time.time()
        if now-self.last_tick<20: return
        self.last_tick=now
        self.db.set('scheduler:attempt',now)
        warning=self.db.setting('media_warning')
        if warning:
            self.alert(warning); self.db.set('media_warning','')
        failed=[]
        # Opening/countdowns do not depend on successful referral checks or queue delivery.
        for name in ('expiry','schedule','announcements','referrals','activity','ads','diagnostics'):
            self.db.set('task:'+name+':attempt',time.time())
            try:
                getattr(self,'task_'+name)(now)
                self.db.set('task:'+name+':ok',time.time())
                self.db.set('task:'+name+':error','')
            except Exception as error:
                failed.append(name)
                self.record_failure('task:'+name,error)
        try:
            self.jobs()
        except Exception as error:
            failed.append('jobs')
            self.record_failure('task:jobs',error)
        self.db.set('scheduler:completed',time.time())
        self.db.set('scheduler:failed',','.join(failed))
