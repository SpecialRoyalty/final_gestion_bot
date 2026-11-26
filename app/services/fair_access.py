"""Invitation overflow and inactivity measured only during confirmed writable time."""
import html
import json
import time
import uuid
from app.keyboards.inline import button, keyboard
from app.services.telegram import APIError, DeferredRequest, NetworkError, blocked_recipient
from app.utils.core import active_access

INVITE_NEWS=('📢 Les invitations en surplus passent maintenant aux autres objectifs ! '
             'Chaque groupe conserve son lien personnel : il remplit d’abord ce groupe, puis l’objectif auquel il manque le moins d’invitations. '
             'Si tout est complet, les nouvelles invitations restent en réserve. Tes invitations déjà acquises et tes accès sont conservés. '
             'Les périodes où le groupe est fermé ou bloqué par le locker ne comptent plus comme de l’inactivité. '
             'Si tu as été exclu à tort, contacte l’administration pour vérifier ton accès.')

class FairAccess:
    def initialize_fair_access(self):
        if self.db.setting('fair_access:initialized'):return
        with self.db.transaction():
            self.db.run("INSERT INTO rehabilitation(uid,gid,created) SELECT a.uid,a.gid,? FROM access a JOIN members m ON m.uid=a.uid AND m.gid=a.gid JOIN groups g ON g.id=a.gid JOIN users u ON u.id=a.uid WHERE a.revoked=1 AND (a.expires IS NULL OR a.expires>?) AND m.present=0 AND g.locker=1 AND g.role IN ('chat','always') AND u.banned=0 AND u.permanent=0 ON CONFLICT DO NOTHING",(time.time(),time.time()))
            self.db.run("INSERT INTO feature_notifications(uid) SELECT id FROM users WHERE menu IS NOT NULL AND banned=0 AND permanent=0 ON CONFLICT DO NOTHING")
            # Retire legacy wall-clock warnings; the new clock starts with a grace period.
            for job in self.db.all("SELECT id,payload FROM jobs WHERE method='sendMessage'"):
                text=json.loads(job['payload']).get('text','')
                if any(text.startswith(prefix) for prefix in ('⚠️ Partage un média dans ','⚠️ Share a photo or video in ','⚠️ Condividi una foto o un video in ','⚠️ Comparte una foto o un vídeo en ')):
                    self.db.run('DELETE FROM jobs WHERE id=?',(job['id'],))
            self.db.run('UPDATE members SET warned=0 WHERE warned!=0')
            self.db.set('fair_access:initialized',time.time())

    def lock_inviter(self,uid):
        self.user(uid)
        if not self.db.sqlite:self.db.one('SELECT id FROM users WHERE id=? FOR UPDATE',(uid,))

    def assign_invitation(self,r):
        """Caller holds inviter lock and transaction. A validated invite is assigned once."""
        uid=r['inviter'];iid=r['invitee']
        origin=self.db.one('SELECT gid FROM referral_origins WHERE invitee=?',(iid,))
        if not origin:
            target=self.db.one('SELECT gid FROM referral_targets WHERE invitee=?',(iid,))
            origin={'gid':target['gid'] if target else ''}
            self.db.run('INSERT INTO referral_origins(invitee,gid) VALUES (?,?) ON CONFLICT DO NOTHING',(iid,origin['gid']))
        candidates=[]
        for g in self.db.all("SELECT * FROM groups WHERE enabled=1 AND role IN ('chat','always') AND goal>0"):
            a=self.access(uid,g['id'])
            if active_access(a,time.time()):continue
            remaining=g['goal']-max(0,self.count(uid,g['id'])-(a['base'] if a else 0))
            if remaining>0:candidates.append((g['id']!=origin['gid'],remaining,g['id']))
        self.db.run('DELETE FROM referral_targets WHERE invitee=?',(iid,))
        if candidates:
            gid=min(candidates)[2]
            self.db.run('INSERT INTO referral_targets(invitee,gid) VALUES (?,?)',(iid,gid))
            self.db.run('DELETE FROM referral_reserves WHERE invitee=?',(iid,))
        else:
            self.db.run('INSERT INTO referral_reserves(invitee,uid) VALUES (?,?) ON CONFLICT DO NOTHING',(iid,uid))

    def allocate_reserve(self,uid):
        if not self.db.one('SELECT 1 FROM referral_reserves WHERE uid=? LIMIT 1',(uid,)):return
        with self.db.transaction():
            self.lock_inviter(uid)
            for row in self.db.all('SELECT r.* FROM referral_reserves s JOIN referrals r ON r.invitee=s.invitee WHERE s.uid=? AND r.valid=1 ORDER BY r.joined,r.invitee LIMIT 100',(uid,)):
                self.assign_invitation(row)
                if self.db.one('SELECT 1 FROM referral_reserves WHERE invitee=?',(row['invitee'],)):break

    def task_activity(self,now):
        # No retroactive charging for time before this version or long unobserved gaps.
        for g in self.db.all("SELECT * FROM groups WHERE role IN ('chat','always')"):
            writable=bool(g['enabled'] and g['opened'] and not self.db.one('SELECT 1 FROM group_pauses WHERE chat=?',(g['chat'],)))
            if writable:
                roster=json.loads(self.db.setting('admin_roster:'+str(g['chat']),'{}'))
                # Missing rights/roster evidence suspends punishment instead of fetching per member.
                writable=roster.get('until',0)>now
            else:roster={}
            if not writable:
                self.db.run('UPDATE activity_clocks SET eligible=0,checked=? WHERE gid=? AND eligible!=0',(now,g['id']))
                continue
            admins=sorted(set(roster.get('ids',[]))|self.owners)
            eligible='CASE WHEN m.uid IN ('+','.join('?' for _ in admins)+') THEN 0 ELSE 1 END' if admins else '1'
            # One round trip per group, not SELECT + UPDATE for each member.
            sql=("INSERT INTO activity_clocks(uid,gid,media,joined,checked,seconds,eligible) "
                 "SELECT m.uid,m.gid,m.media,m.joined,?,CASE WHEN c.media=m.media AND c.joined=m.joined THEN c.seconds+"
                 "CASE WHEN c.eligible=1 AND ?-c.checked BETWEEN 0 AND 120 THEN ?-c.checked ELSE 0 END ELSE 0 END,"+eligible+
                 " FROM members m LEFT JOIN activity_clocks c ON c.uid=m.uid AND c.gid=m.gid WHERE m.gid=? AND m.present=1 "
                 "ON CONFLICT(uid,gid) DO UPDATE SET media=excluded.media,joined=excluded.joined,checked=excluded.checked,seconds=excluded.seconds,eligible=excluded.eligible")
            self.db.run(sql,(now,now,now,*admins,g['id']))
            for m in self.db.all('SELECT m.*,c.seconds FROM members m JOIN activity_clocks c ON c.uid=m.uid AND c.gid=m.gid WHERE m.gid=? AND m.present=1 AND c.eligible=1 AND c.seconds>=?',(g['id'],3*86400)):
                seconds=m['seconds']
                if seconds>=4*86400:
                    # Deterministic id prevents piling up repeated bans while the worker is busy.
                    payload=dict(chat_id=g['chat'],user_id=m['uid'],_activity={'gid':g['id'],'media':m['media'],'joined':m['joined']})
                    self.db.run('INSERT INTO jobs(id,method,payload,due) VALUES (?,?,?,?) ON CONFLICT DO NOTHING',('activity:'+g['id']+':'+str(m['uid']),'banChatMember',json.dumps(payload),now))
                elif seconds>=3*86400 and not m['warned']:
                    self.enqueue('sendMessage',dict(chat_id=m['uid'],text=self.tr(m['uid'],'⚠️ Partage un média dans ')+g['title']+self.tr(m['uid'],' pour conserver ton accès. Il reste 24 heures d’ouverture effective ; les fermetures et le locker mettent le délai en pause.')))
                    self.db.run('UPDATE members SET warned=1 WHERE uid=? AND gid=?',(m['uid'],g['id']))

    def perform_activity_job(self,job,payload):
        proof=payload.pop('_activity');gid=proof['gid'];uid=payload['user_id'];g=self.group(gid)
        m=self.db.one('SELECT * FROM members WHERE uid=? AND gid=?',(uid,gid))
        c=self.db.one('SELECT * FROM activity_clocks WHERE uid=? AND gid=?',(uid,gid))
        roster=json.loads(self.db.setting('admin_roster:'+str(g['chat']),'{}')) if g else {}
        valid=(g and g['enabled'] and g['opened'] and g['chat']==payload['chat_id'] and m and m['present'] and
               m['media']==proof['media'] and m['joined']==proof['joined'] and c and c['seconds']>=4*86400 and
               c['eligible'] and time.time()-c['checked']<=120 and roster.get('until',0)>time.time() and
               uid not in self.owners and uid not in roster.get('ids',[]))
        if valid:
            self.api.call('banChatMember',**payload)
            with self.db.transaction():
                latest=self.db.one('SELECT * FROM members WHERE uid=? AND gid=?',(uid,gid))
                if latest and latest['media']==proof['media'] and latest['joined']==proof['joined']:
                    self.db.run('UPDATE members SET present=0 WHERE uid=? AND gid=?',(uid,gid))
                    self.db.run('UPDATE access SET revoked=1,base=? WHERE uid=? AND gid=?',(self.count(uid,gid),uid,gid))
                    self.db.run('INSERT INTO audit(id,actor,action,created) VALUES (?,?,?,?)',(uuid.uuid4().hex,uid,'inactivity_confirmed:'+gid,time.time()))
                else:
                    self.enqueue('unbanChatMember',dict(chat_id=g['chat'],user_id=uid,only_if_banned=True,_unless_banned=True))
        self.db.run('DELETE FROM jobs WHERE id=?',(job['id'],))

    def queue_rehabilitation(self,uid,gid):
        u=self.user(uid)
        if u['banned'] or u['permanent'] or self.db.setting('link_ban:'+str(uid)):return
        self.db.run('INSERT INTO rehabilitation(uid,gid,created) VALUES (?,?,?) ON CONFLICT DO NOTHING',(uid,gid,time.time()))

    def rehabilitation_callback(self,uid,data):
        if data!='rehab' and not data.startswith(('rehabyes:','rehabno:')):return False
        if uid not in self.owners:return True
        if data!='rehab':
            _,person,gid=data.split(':',2);person=int(person)
            with self.db.transaction():
                self.lock_inviter(person)
                item=self.db.one("SELECT * FROM rehabilitation WHERE uid=? AND gid=? AND state='review'",(person,gid))
                u=self.user(person);a=self.access(person,gid);g=self.group(gid)
                if item:
                    restore=data.startswith('rehabyes:') and g and a and a['revoked'] and (a['expires'] is None or a['expires']>time.time()) and not u['banned'] and not u['permanent'] and not self.db.setting('link_ban:'+str(person))
                    if restore:
                        for job in self.db.all("SELECT id,payload FROM jobs WHERE method='banChatMember'"):
                            payload=json.loads(job['payload'])
                            if payload.get('user_id')==person and payload.get('chat_id')==g['chat'] and not payload.get('_if_banned'):
                                self.db.run('DELETE FROM jobs WHERE id=?',(job['id'],))
                        self.db.run('UPDATE access SET revoked=0 WHERE uid=? AND gid=?',(person,gid))
                        self.db.run('DELETE FROM activity_clocks WHERE uid=? AND gid=?',(person,gid))
                        self.enqueue('unbanChatMember',dict(chat_id=g['chat'],user_id=person,only_if_banned=True,_unless_banned=True))
                        self.enqueue('sendMessage',dict(chat_id=person,text=self.tr(person,'Ton accès a été rétabli après vérification du locker. Retrouve ton groupe dans le bot.'),reply_markup=keyboard([button('🚪 Mon accès',url=self.deep('g_'+gid))])))
                    self.db.run('UPDATE rehabilitation SET state=? WHERE uid=? AND gid=?',('restored' if restore else 'dismissed',person,gid))
                    self.db.run('INSERT INTO audit(id,actor,action,created) VALUES (?,?,?,?)',(uuid.uuid4().hex,uid,data,time.time()))
        rows=self.db.all("SELECT r.*,g.title FROM rehabilitation r JOIN groups g ON g.id=r.gid WHERE r.state='review' ORDER BY r.created,r.uid,r.gid LIMIT 1")
        text='🛟 Réhabilitations locker\n\nLes anciennes exclusions ne précisaient pas si le groupe était fermé. Vérifie ce cas avant de rétablir son accès. Les bannissements de sécurité restent exclus.'
        buttons=[]
        if rows:
            r=rows[0];text+='\n\nUtilisateur '+str(r['uid'])+' · '+html.escape(r['title'])
            buttons=[[button('✅ Confirmer : exclu à tort',f"rehabyes:{r['uid']}:{r['gid']}")],[button('Ignorer ce cas',f"rehabno:{r['uid']}:{r['gid']}")]]
        else:text+='\n\nAucun cas en attente.'
        self.screen(uid,text,buttons+[[button('⬅️ État du réseau','health')]])
        return True

    def feature_notification_step(self):
        now=time.time()
        if now<float(self.db.setting('feature_news:next','0')):return
        row=self.db.one("SELECT * FROM feature_notifications WHERE state='ready' AND due<=? ORDER BY uid LIMIT 1",(now,))
        if not row:return
        uid=row['uid'];u=self.user(uid)
        if u['banned'] or u['permanent'] or self.db.setting('dm_blocked:'+str(uid)):
            self.db.run("UPDATE feature_notifications SET state='unreachable' WHERE uid=?",(uid,));return
        self.db.run("UPDATE feature_notifications SET state='sending' WHERE uid=?",(uid,))
        self.db.set('feature_news:next',now+1)  # Separate, bounded background broadcast.
        try:
            self.api.call('sendMessage',chat_id=uid,text=self.tr(uid,INVITE_NEWS),reply_markup=keyboard([button(self.tr(uid,'🔗 Inviter mes amis'),url=self.deep('invite'))]))
        except APIError as error:
            if error.code==429:
                self.db.run("UPDATE feature_notifications SET state='ready',due=? WHERE uid=?",(now+max(.1,error.retry),uid))
                self.db.set('feature_news:next',now+max(.1,error.retry));return
            state='unreachable' if blocked_recipient(error) else ('uncertain' if isinstance(error,NetworkError) or error.code>=500 else 'failed')
            self.db.run('UPDATE feature_notifications SET state=? WHERE uid=?',(state,uid))
            if state=='unreachable':self.db.set('dm_blocked:'+str(uid),now)
            else:self.record_failure('feature_news',error)
            return
        self.db.run("UPDATE feature_notifications SET state='sent' WHERE uid=?",(uid,))
