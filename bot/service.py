import json
import uuid
from .domain import now, config, active, eligible_order
from .telegram import TelegramError

class Service:
    def __init__(self,db,tg,owners,bot_id=0):
        self.db,self.tg,self.owners,self.bot_id=db,tg,set(owners),bot_id

    def group(self,gid):
        g=self.db.one('SELECT * FROM groups WHERE id=?',(gid,))
        if not g: raise ValueError('Groupe introuvable.')
        return g

    def banned(self,uid):
        return self.db.one('SELECT * FROM bans WHERE uid=?',(uid,))

    def owned(self,uid,gid):
        return active(self.db.one('SELECT * FROM access WHERE uid=? AND gid=?',(uid,gid)),now())

    def upsert_user(self,u,started=False):
        self.db.run('INSERT INTO users(id,name,started) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,started=CASE WHEN excluded.started=1 THEN 1 ELSE users.started END',(u['id'],u.get('first_name','Membre'),int(started)))

    def audit(self,uid,action,detail):
        self.db.run('INSERT INTO audit VALUES(?,?,?,?,?)',(uuid.uuid4().hex,uid,action,str(detail),now()))

    def admin(self,uid,gid=None):
        if uid in self.owners: return True
        if gid:
            g=self.group(gid)
            try:
                return self.tg.call('getChatMember',chat_id=g['chat_id'],user_id=uid)['status'] in ('creator','administrator')
            except TelegramError: return False
        return False

    def require_admin(self,uid,gid=None):
        if not self.admin(uid,gid): raise ValueError('Action réservée aux administrateurs autorisés.')

    def enqueue(self,key,kind,payload,due=None):
        if 'gid' in payload:payload=dict(payload,chat_id=self.group(payload['gid'])['chat_id'])
        self.db.run('INSERT INTO jobs VALUES(?,?,?,?,0) ON CONFLICT(id) DO NOTHING',(key,kind,json.dumps(payload),now() if due is None else due))

    def notify(self,uid,text):
        if self.db.one('SELECT id FROM users WHERE id=? AND started=1',(uid,)):
            self.enqueue('notice:'+uuid.uuid4().hex,'notify',{'uid':uid,'text':text})

    def alert(self,key,text):
        old=self.db.one('SELECT * FROM alerts WHERE key=?',(key,))
        self.db.run('INSERT INTO alerts VALUES(?,?,?,0) ON CONFLICT(key) DO UPDATE SET body=excluded.body,resolved=0',(key,text,now()))
        if not old or old['resolved']:
            for uid in self.owners: self.notify(uid,'🚨 '+text)

    def grant(self,uid,gid,source,days=0):
        expires=now()+days*86400 if days else 0
        self.db.run('INSERT INTO access VALUES(?,?,?,?) ON CONFLICT(gid,uid) DO UPDATE SET source=excluded.source,expires=excluded.expires',(gid,uid,source,expires))
        self.notify(uid,f'✅ Ton accès à {self.group(gid)["name"]} est disponible. Retrouve-le dans Mes accès.')

    def count(self,uid,gid):
        ch=self.db.one('SELECT started FROM challenges WHERE gid=? AND uid=?',(gid,uid))
        if not ch:return 0
        return self.db.one('SELECT COUNT(*) AS n FROM referrals WHERE inviter=? AND valid=1 AND joined>=?',(uid,ch['started']))['n']

    def challenge(self,uid,gid):
        g=self.group(gid)
        if self.banned(uid): raise ValueError('Ton accès est suspendu.')
        if g['role'] not in ('chat','always') or not g['healthy'] or not config(g)['enabled']:
            raise ValueError('Défi indisponible.')
        self.db.run('INSERT INTO challenges VALUES(?,?,?) ON CONFLICT(gid,uid) DO NOTHING',(gid,uid,now()))

    def referral_link(self,uid):
        ban=self.banned(uid)
        if ban and ban['permanent']:raise ValueError('Accès indisponible.')
        portal=self.db.one("SELECT * FROM groups WHERE role='portal' AND healthy=1")
        if not portal:raise ValueError('Le portail est indisponible.')
        old=self.db.one("SELECT url FROM links WHERE kind='ref' AND gid=? AND uid=?",(portal['id'],uid))
        if old:return old['url']
        link=self.tg.call('createChatInviteLink',chat_id=portal['chat_id'],name=f'ref-{uid}')['invite_link']
        self.db.run('INSERT INTO links VALUES(?,?,?,?,0)',(link,'ref',portal['id'],uid))
        return link

    def entry_link(self,uid,gid):
        g=self.group(gid)
        if self.banned(uid):raise ValueError('Ton accès est suspendu.')
        if not g['healthy'] or not config(g)['enabled']:raise ValueError('Groupe temporairement indisponible.')
        if g['role']=='proof' and self.db.one('SELECT uid FROM visits WHERE uid=?',(uid,)):
            raise ValueError('Ta visite découverte a déjà été utilisée.')
        if g['role'] not in ('free','proof') and not self.owned(uid,gid):raise ValueError('Accès non débloqué.')
        old=self.db.one("SELECT url FROM links WHERE kind='entry' AND gid=? AND uid=? AND expires>?",(gid,uid,now()+30))
        if old:return old['url']
        link=self.tg.call('createChatInviteLink',chat_id=g['chat_id'],name=f'access-{uid}',expire_date=now()+600,creates_join_request=True)['invite_link']
        self.db.run('INSERT INTO links VALUES(?,?,?,?,?)',(link,'entry',gid,uid,now()+600))
        return link

    def ban(self,uid,permanent=False,reason='modération'):
        if uid in self.owners:return
        self.db.run('INSERT INTO bans VALUES(?,?,?,?) ON CONFLICT(uid) DO UPDATE SET permanent=CASE WHEN bans.permanent=1 THEN 1 ELSE excluded.permanent END,reason=excluded.reason,created=excluded.created',(uid,int(permanent),reason,now()))
        for g in self.db.all("SELECT * FROM groups WHERE role!='pending'"):
            if g['role']=='portal' and not permanent:continue
            self.enqueue(f'ban:{g["id"]}:{uid}','ban',{'uid':uid,'gid':g['id']})
        self.audit(0,'ban',f'{uid}: {reason}')

    def checkout(self,uid):
        if not self.db.setting('sales',False):raise ValueError('Les ventes ne sont pas encore activées.')
        old=self.db.one("SELECT * FROM orders WHERE uid=? AND status IN ('pending','checkout')",(uid,))
        if old:return old
        groups=self.db.all('SELECT g.* FROM groups g JOIN cart c ON c.gid=g.id WHERE c.uid=?',(uid,))
        owned={g['id'] for g in groups if self.owned(uid,g['id'])}
        items=eligible_order(groups,owned,bool(self.banned(uid)))
        order=dict(id=uuid.uuid4().hex,uid=uid,items=json.dumps(items),total=sum(i['price'] for i in items),status='pending',created=now())
        self.db.run('INSERT INTO orders(id,uid,items,total,status,created) VALUES(?,?,?,?,?,?)',tuple(order.values()))
        return self.db.one('SELECT * FROM orders WHERE id=?',(order['id'],))

    def validate_order(self,order,uid,total,currency):
        if not order or order['uid']!=uid or order['status'] not in ('pending','checkout'):
            raise ValueError('Commande expirée ou déjà réglée.')
        if currency!='XTR' or total!=order['total']:raise ValueError('Montant incorrect.')
        items=json.loads(order['items'])
        groups=[self.group(i['gid']) for i in items]
        fresh=eligible_order(groups,{g['id'] for g in groups if self.owned(uid,g['id'])},bool(self.banned(uid)))
        if fresh!=items:raise ValueError('Le panier a changé. Reviens dans le bot pour le vérifier.')

    def paid(self,uid,payment):
        order=self.db.one('SELECT * FROM orders WHERE id=?',(payment['invoice_payload'],))
        if not order or order['uid']!=uid or payment['currency']!='XTR' or payment['total_amount']!=order['total']:
            self.alert('payment:'+payment['telegram_payment_charge_id'],'Paiement incohérent : intervention nécessaire.');return
        if order['status']=='paid':return
        if order['status'] not in ('pending','checkout'):
            self.alert('late-payment:'+order['id'],'Paiement reçu après annulation : remboursement automatique demandé.')
            self.enqueue('late-refund:'+order['id'],'refund',{'uid':uid,'charge':payment['telegram_payment_charge_id']})
            return
        with self.db.transaction():
            changed=self.db.run("UPDATE orders SET status='paid',charge=? WHERE id=? AND status IN ('pending','checkout')",(payment['telegram_payment_charge_id'],order['id'])).rowcount
            if not changed:return
            for item in json.loads(order['items']):
                self.grant(uid,item['gid'],'stars',item['days'])
                self.db.run('INSERT INTO access_orders VALUES(?,?,?) ON CONFLICT(gid,uid) DO UPDATE SET order_id=excluded.order_id',(item['gid'],uid,order['id']))
            self.db.run('DELETE FROM cart WHERE uid=?',(uid,))
        self.audit(uid,'payment',order['id'])

    def cancel_order(self,uid):
        o=self.db.one("SELECT * FROM orders WHERE uid=? AND status IN ('pending','checkout')",(uid,))
        if o and o['status']=='checkout' and o['approved_until']>now():raise ValueError('Un paiement est en cours. Patiente quelques minutes.')
        self.db.run("UPDATE orders SET status='cancelled' WHERE uid=? AND status IN ('pending','checkout')",(uid,))

    def health(self,g):
        try:
            member=self.tg.call('getChatMember',chat_id=g['chat_id'],user_id=self.bot_id)
            required=('can_delete_messages','can_invite_users','can_restrict_members')
            ok=member['status']=='administrator' and all(member.get(k,False) for k in required)
            count=self.tg.call('getChatMemberCount',chat_id=g['chat_id'])
            self.db.run('UPDATE groups SET healthy=?,checked=?,member_count=? WHERE id=?',(int(ok),now(),max(0,count-1),g['id']))
            if not ok: self.alert('health:'+g['id'],g['name']+' : permissions administrateur incomplètes. Entrées et ventes suspendues.')
            else:self.db.run('UPDATE alerts SET resolved=1 WHERE key=?',('health:'+g['id'],))
            return ok
        except TelegramError:
            self.db.run('UPDATE groups SET healthy=0,checked=? WHERE id=?',(now(),g['id']))
            self.alert('health:'+g['id'],g['name']+' : groupe inaccessible. Vérifier le bot ou remplacer le groupe.')
            return False

    def replace(self,uid,oldid,newid):
        self.require_admin(uid)
        old,new=self.group(oldid),self.group(newid)
        if oldid==newid or new['role']!='pending':raise ValueError('Choisis un nouveau groupe non configuré.')
        self.require_admin(uid,newid)
        if not self.health(new):raise ValueError('Permissions manquantes dans le nouveau groupe.')
        with self.db.transaction():
            self.db.run('DELETE FROM groups WHERE id=?',(newid,))
            self.db.run('UPDATE groups SET chat_id=?,healthy=1,member_count=?,checked=? WHERE id=?',(new['chat_id'],new['member_count'],now(),oldid))
            self.db.run('DELETE FROM posts WHERE gid=?',(oldid,))
            self.db.run('DELETE FROM links WHERE gid=?',(oldid,))
            self.db.run('DELETE FROM membership WHERE gid=?',(oldid,))
            self.db.run('DELETE FROM messages WHERE gid=?',(oldid,))
            self.db.run('DELETE FROM media WHERE gid=?',(oldid,))
            self.db.run('DELETE FROM media_queue WHERE gid=?',(oldid,))
            for job in self.db.all('SELECT * FROM jobs'):
                if json.loads(job['payload']).get('gid')==oldid:self.db.run('DELETE FROM jobs WHERE id=?',(job['id'],))
        for a in self.db.all('SELECT * FROM access WHERE gid=?',(oldid,)):
            if active(a,now()):self.notify(a['uid'],f'🔄 {old["name"]} a été remplacé. Ton accès est conservé : ouvre Mes accès pour ton nouveau lien.')
        for b in self.db.all('SELECT * FROM bans'):
            if old['role']!='portal' or b['permanent']:self.enqueue(f'ban:{oldid}:{b["uid"]}','ban',{'gid':oldid,'uid':b['uid']})
        self.audit(uid,'replace',oldid)
