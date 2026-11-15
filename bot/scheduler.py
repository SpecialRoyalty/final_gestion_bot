import json
import logging
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from .domain import now,config,window,countdown,active
from .telegram import TelegramError

LOG=logging.getLogger(__name__)
SEND_FIELDS=('can_send_messages','can_send_audios','can_send_documents','can_send_photos','can_send_videos','can_send_video_notes','can_send_voice_notes','can_send_polls','can_send_other_messages','can_add_web_page_previews','can_change_info','can_invite_users','can_pin_messages','can_manage_topics')

def permissions(opened):
    p={k:False for k in SEND_FIELDS}
    if opened:
        for k in ('can_send_messages','can_send_photos','can_send_videos'):p[k]=True
    return p

class Scheduler:
    def __init__(self,s,username,tz):self.s=s;self.username=username;self.tz=tz;self.last=0

    def post(self,g,slot,text,buttons=None,photo=None):
        s=self.s;db=s.db
        body=json.dumps([text,buttons,photo],ensure_ascii=False)
        old=db.one('SELECT * FROM posts WHERE gid=? AND slot=?',(g['id'],slot))
        if old and old['body']==body:return
        kwargs={'chat_id':g['chat_id'],'reply_markup':{'inline_keyboard':buttons or []}}
        if old:
            try:s.tg.call('deleteMessage',chat_id=g['chat_id'],message_id=old['mid'])
            except TelegramError as e:
                if 'not found' not in e.description:
                    s.alert('post-delete:'+g['id'],g['name']+' : une ancienne annonce ne peut pas être supprimée.')
        if photo:r=s.tg.call('sendPhoto',photo=photo,caption=text[:1024],**kwargs)
        else:r=s.tg.call('sendMessage',text=text[:4096],**kwargs)
        db.run('INSERT INTO posts VALUES(?,?,?,?,?) ON CONFLICT(gid,slot) DO UPDATE SET mid=excluded.mid,body=excluded.body,updated=excluded.updated',(g['id'],slot,r['message_id'],body,now()))

    def remove(self,g,slot):
        row=self.s.db.one('SELECT * FROM posts WHERE gid=? AND slot=?',(g['id'],slot))
        if row:
            self.s.enqueue(f'del:{g["id"]}:{row["mid"]}','delete',{'gid':g['id'],'mid':row['mid']})
            self.s.db.run('DELETE FROM posts WHERE gid=? AND slot=?',(g['id'],slot))

    def share_buttons(self):
        return [[{'text':'📨 J’invite','url':f'https://t.me/{self.username}?start=invite'}]]

    def locked(self,g):
        c=config(g)
        return c['locker'] and g['member_count']<c['threshold']

    def set_open(self,g,opened):
        self.s.tg.call('setChatPermissions',chat_id=g['chat_id'],permissions=permissions(opened),use_independent_chat_permissions=True)

    def close(self,g):
        self.set_open(g,False)
        for m in self.s.db.all('SELECT mid FROM messages WHERE gid=?',(g['id'],)):
            self.s.enqueue(f'del:{g["id"]}:{m["mid"]}','delete',{'gid':g['id'],'mid':m['mid']})
        self.remove(g,'rules');self.remove(g,'closing')

    def session(self,day,groups):
        db=self.s.db;key=day.isoformat()
        old=db.one('SELECT * FROM sessions WHERE day=?',(key,))
        if old:return old
        if not groups:return None
        previous=db.one('SELECT gid FROM sessions WHERE day<? ORDER BY day DESC LIMIT 1',(key,))
        last={r['gid']:r['day'] for r in db.all('SELECT gid,MAX(day) day FROM sessions GROUP BY gid')}
        choices=[g for g in groups if not previous or g['id']!=previous['gid']] or groups
        g=min(choices,key=lambda x:(last.get(x['id'],''),x['id']))
        a,b=window(day,config(g)['slot'],self.tz)
        db.run('INSERT INTO sessions(day,gid,starts,ends) VALUES(?,?,?,?)',(key,g['id'],a,b))
        return db.one('SELECT * FROM sessions WHERE day=?',(key,))

    def tick(self):
        t=now();s=self.s;db=s.db
        self.jobs()
        if t-self.last<30:return
        self.last=t
        # Referral validation checks actual membership, not just elapsed time.
        portal=db.one("SELECT * FROM groups WHERE role='portal' AND healthy=1")
        if portal:
            for r in db.all('SELECT * FROM referrals WHERE valid=0 AND joined<=?',(t-300,)):
                from .domain import present
                try:inside=present(s.tg.call('getChatMember',chat_id=portal['chat_id'],user_id=r['invitee']))
                except TelegramError:continue
                child=db.one('SELECT invitee FROM referrals WHERE inviter=? AND valid=1 LIMIT 1',(r['invitee'],))
                # Strict mode uses a direct recruit who stayed 5 min; avoids infinite recursive validation.
                if db.setting('ref_strict',False) and not child:
                    child=next((x for x in db.all('SELECT * FROM referrals WHERE inviter=? AND joined<=?',(r['invitee'],t-300)) if db.one('SELECT uid FROM membership WHERE gid=? AND uid=? AND present=1',(portal['id'],x['invitee']))),None)
                if inside and not s.banned(r['invitee']) and (not db.setting('ref_strict',False) or child):
                    db.run('UPDATE referrals SET valid=1 WHERE invitee=?',(r['invitee'],))
        for ch in db.all('SELECT * FROM challenges'):
            if ch['gid']=='unban':
                b=s.banned(ch['uid'])
                if b and not b['permanent'] and s.count(ch['uid'],'unban')>=db.setting('unban_goal',15):
                    db.run('DELETE FROM bans WHERE uid=?',(ch['uid'],))
                    for g in db.all('SELECT * FROM groups'):
                        s.enqueue(f'unban:{g["id"]}:{ch["uid"]}','unban',{'gid':g['id'],'uid':ch['uid']})
                    s.notify(ch['uid'],'✅ Ta demande de retour a été validée.')
                continue
            g=s.group(ch['gid'])
            if not s.banned(ch['uid']) and not s.owned(ch['uid'],g['id']) and s.count(ch['uid'],g['id'])>=config(g)['goal']:
                s.grant(ch['uid'],g['id'],'invites')
        for a in db.all('SELECT * FROM access WHERE expires>0 AND expires<=?',(t,)):
            s.enqueue(f'expire:{a["gid"]}:{a["uid"]}','expire',{'gid':a['gid'],'uid':a['uid']})
        for o in db.all("SELECT * FROM orders WHERE status='pending' AND reminded=0 AND created<=?",(t-172800,)):
            items=[i for i in json.loads(o['items']) if not s.owned(o['uid'],i['gid'])]
            if items and not s.banned(o['uid']):
                s.notify(o['uid'],'Tu as oublié quelque chose ?\n\nTes accès t’attendent encore :\n'+ '\n'.join(f'⭐ {i["name"]} : {i["price"]} Stars' for i in items)+'\n\nIl ne te reste plus qu’à finaliser 👇\nOuvre Mon panier dans le menu.')
            db.run('UPDATE orders SET reminded=1 WHERE id=?',(o['id'],))
        groups=db.all("SELECT * FROM groups WHERE role!='pending'")
        for g in groups:
            try:
                if t-g['checked']>=7200:s.health(g)
                if g['healthy']:self.inactivity(g)
            except Exception as e:s.alert('tick:'+g['id'],g['name']+' : contrôle incomplet ('+type(e).__name__+').')
        groups=db.all("SELECT * FROM groups WHERE role!='pending' AND healthy=1")
        rot=[g for g in groups if g['role']=='chat' and config(g)['enabled']]
        today=datetime.fromtimestamp(t,ZoneInfo(self.tz)).date()
        # Close missed sessions before opening a new one, including after downtime.
        for session in db.all("SELECT * FROM sessions WHERE state IN ('waiting','open') AND ends<=?",(t,)):
            g=s.group(session['gid'])
            try:
                self.close(g)
                db.run("UPDATE sessions SET state='closed' WHERE day=?",(session['day'],))
            except Exception:s.alert('close:'+g['id'],g['name']+' : fermeture en échec, nouvelle ouverture bloquée.');return
        session=self.session(today,rot)
        previous=db.one("SELECT * FROM sessions WHERE state='open' AND ends>? ORDER BY starts LIMIT 1",(t,))
        if session and session['ends']<=t:
            session=self.session(today+timedelta(days=1),rot)
        current=previous or session
        opened=None
        if current:
            g=s.group(current['gid']);c=config(g)
            if current['starts']<=t<current['ends']:
                if current['state']=='open':opened=g
                elif g['healthy'] and not self.locked(g):
                    self.set_open(g,True)
                    db.run("UPDATE sessions SET state='open' WHERE day=?",(current['day'],))
                    self.post(g,'status','🟢 Groupe ouvert ! Vous pouvez envoyer des messages, photos et vidéos.')
                    opened=g
                else:self.post(g,'status',f'🔒 Le groupe démarrera avec au moins {c["threshold"]} membres présents.\nActuellement : {g["member_count"]}/{c["threshold"]}',self.share_buttons())
            elif t<current['starts']:
                self.set_open(g,False)
                self.post(g,'status','⏳ Le groupe ouvrira dans '+countdown(current['starts']-t)+'.',self.share_buttons())
        for g in groups:
            try:
                c=config(g)
                if not c['enabled'] and g['role']!='portal':self.set_open(g,False);continue
                if g['role']=='chat':
                    if not opened or opened['id']!=g['id']:
                        self.set_open(g,False)
                        if current and g['id']!=current['gid']:
                            nextg=s.group(current['gid'])
                            self.post(g,'status',f'Prochaine session du réseau : {nextg["name"]}.',self.share_buttons())
                    else:
                        self.recurring(g)
                        remaining=current['ends']-t
                        if remaining<=1800:
                            bucket=next((x for x in (1,2,3,4,5,15,30) if remaining<=x*60),30)
                            self.post(g,'closing',f'⏳ Fermeture dans {bucket} minute(s).')
                if g['role'] in ('always','vip'):
                    key='launched:'+g['id']
                    launched=db.setting(key,False)
                    if not launched and not self.locked(g):db.set(key,True);launched=True
                    self.set_open(g,launched)
                    if not launched:self.post(g,'status',f'🔒 Ouverture à {c["threshold"]} membres. Actuellement : {g["member_count"]}',self.share_buttons())
                    else:
                        self.remove(g,'status')
                        self.recurring(g)
                if g['role']=='free':self.free(g,t,today)
                if opened and g['role'] in ('portal','chat') and opened['id']!=g['id']:
                    self.post(g,'network',f'🔥 {opened["name"]} est ouvert !\nDécouvre le groupe et débloque ton accès.',[[{'text':'💬 Voir le groupe','url':f'https://t.me/{self.username}?start=g_{opened["id"]}'}]]+self.share_buttons())
                else:self.remove(g,'network')
            except Exception as e:
                s.alert('schedule:'+g['id'],g['name']+' : programmation en échec ('+type(e).__name__+').')

    def free(self,g,t,today):
        c=config(g);day=today-timedelta(days=(today.weekday()-c['weekday'])%7)
        a,b=window(day,c['slot'],self.tz)
        key='free-open:'+g['id'];was=self.s.db.setting(key,False)
        opened=a<=t<b and (was or not self.locked(g))
        self.set_open(g,opened)
        if was and not opened:self.close(g)
        self.s.db.set(key,opened)
        if opened:
            self.recurring(g);self.post(g,'status','🟢 Groupe chat gratuit ouvert !')
            if b-t<=1800:
                bucket=next((x for x in (1,2,3,4,5,15,30) if b-t<=x*60),30)
                self.post(g,'closing',f'⏳ Fermeture dans {bucket} minute(s).')
        else:
            if a<=t<b:text=f'🔒 Ouverture à {c["threshold"]} membres. Actuellement : {g["member_count"]}'
            else:
                if t>=b:a,b=window(day+timedelta(days=7),c['slot'],self.tz)
                text='🎁 Groupe chat gratuit — ouverture dans '+countdown(a-t)
            self.post(g,'status',text,self.share_buttons())

    def recurring(self,g):
        db=self.s.db;c=config(g);t=now()
        rules=c['rules'] or db.setting('rules','')
        last=db.one("SELECT updated FROM posts WHERE gid=? AND slot='rules'",(g['id'],))
        if rules and (not last or t-last['updated']>=1800):
            # Explicitly replace even when content is identical.
            self.remove(g,'rules');self.post(g,'rules','📜 Règles\n\n'+rules)
        for kind in ('ads','vip_ads'):
            ads=[a for a in c[kind] if a.get('enabled',True)]
            if not ads:continue
            key=kind+':'+g['id'];state=db.setting(key,{'index':0,'at':0,'once':[]})
            ad=ads[state['index']%len(ads)];hours=ad.get('hours',4 if kind=='ads' else 6)
            if hours==0 and state['index']%len(ads) in state['once']:continue
            if t-state['at']>=hours*3600:
                self.remove(g,kind);self.post(g,kind,ad['text'],photo=ad.get('photo'))
                if hours==0:state['once'].append(state['index']%len(ads))
                state.update(index=state['index']+1,at=t);db.set(key,state)

    def inactivity(self,g):
        c=config(g)
        if not c['inactivity'] or g['role'] not in ('chat','always'):return
        for m in self.s.db.all('SELECT * FROM membership WHERE gid=? AND present=1',(g['id'],)):
            if self.s.admin(m['uid'],g['id']):continue
            elapsed=now()-max(m['joined'],m['last_media'])
            if elapsed>=4*86400:
                a=self.s.db.one('SELECT * FROM access WHERE gid=? AND uid=?',(g['id'],m['uid']))
                if a and a['source']=='invites':
                    self.s.db.run('DELETE FROM access WHERE gid=? AND uid=?',(g['id'],m['uid']))
                    self.s.db.run('UPDATE challenges SET started=? WHERE gid=? AND uid=?',(now(),g['id'],m['uid']))
                self.s.enqueue(f'kick:{g["id"]}:{m["uid"]}','kick',{'gid':g['id'],'uid':m['uid']})
            elif elapsed>=3*86400 and not m['warned']:
                self.s.notify(m['uid'],f'⚠️ Pense à partager un média dans {g["name"]} pour conserver ta présence dans le groupe.')
                self.s.db.run('UPDATE membership SET warned=1 WHERE gid=? AND uid=?',(g['id'],m['uid']))

    def jobs(self):
        s=self.s;db=s.db
        for job in db.all('SELECT * FROM jobs WHERE due<=? ORDER BY due LIMIT 30',(now(),)):
            p=json.loads(job['payload']);kind=job['kind']
            try:
                g=s.group(p['gid']) if 'gid' in p else None
                if g and p.get('chat_id',g['chat_id'])!=g['chat_id']:
                    db.run('DELETE FROM jobs WHERE id=?',(job['id'],));continue
                if kind=='notify':
                    s.tg.call('sendMessage',chat_id=p['uid'],text=p['text'],reply_markup={'inline_keyboard':[[{'text':'🏠 Mon espace','callback_data':'home'}]]})
                elif kind=='refund':
                    s.tg.call('refundStarPayment',user_id=p['uid'],telegram_payment_charge_id=p['charge'])
                elif kind=='delete':
                    s.tg.call('deleteMessage',chat_id=g['chat_id'],message_id=p['mid'])
                    db.run('DELETE FROM messages WHERE gid=? AND mid=?',(g['id'],p['mid']))
                elif kind=='revoke':
                    s.tg.call('revokeChatInviteLink',chat_id=g['chat_id'],invite_link=p['url'])
                    db.run('DELETE FROM links WHERE url=?',(p['url'],))
                elif kind in ('ban','kick','expire'):
                    if kind=='expire' and s.owned(p['uid'],g['id']):
                        db.run('DELETE FROM jobs WHERE id=?',(job['id'],));continue
                    if not s.admin(p['uid'],g['id']):
                        s.tg.call('banChatMember',chat_id=g['chat_id'],user_id=p['uid'],revoke_messages=True)
                        if kind!='ban':s.tg.call('unbanChatMember',chat_id=g['chat_id'],user_id=p['uid'],only_if_banned=True)
                        db.run('UPDATE membership SET present=0 WHERE gid=? AND uid=?',(g['id'],p['uid']))
                        if kind=='expire':db.run('DELETE FROM access WHERE gid=? AND uid=? AND expires>0 AND expires<=?',(g['id'],p['uid'],now()))
                elif kind=='unban':s.tg.call('unbanChatMember',chat_id=g['chat_id'],user_id=p['uid'],only_if_banned=True)
                elif kind=='mute':s.tg.call('restrictChatMember',chat_id=g['chat_id'],user_id=p['uid'],permissions=permissions(False),until_date=p['until'],use_independent_chat_permissions=True)
                db.run('DELETE FROM jobs WHERE id=?',(job['id'],))
            except TelegramError as e:
                terminal=(kind=='delete' and ('not found' in e.description or 'can\'t be deleted' in e.description)) or (kind=='notify' and e.code==403)
                if terminal:
                    if kind=='delete':
                        if 'not found' not in e.description:s.alert('delete:'+g['id'],g['name']+' : suppression impossible pour certains messages (droits ou âge).')
                        db.run('DELETE FROM messages WHERE gid=? AND mid=?',(g['id'],p['mid']))
                    db.run('DELETE FROM jobs WHERE id=?',(job['id'],))
                else:self.retry(job,e.retry)
            except Exception:self.retry(job)

    def retry(self,job,retry=0):
        n=job['attempts']+1
        self.s.db.run('UPDATE jobs SET attempts=?,due=? WHERE id=?',(n,now()+max(retry,min(3600,2**min(n,11))),job['id']))
        if n==3:self.s.alert('job:'+job['id'],'Action Telegram en échec, nouvelle tentative planifiée : '+job['kind'])
