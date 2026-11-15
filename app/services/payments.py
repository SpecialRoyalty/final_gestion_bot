"""Manual external payment review. Amounts are stored as integer cents, never floats."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from app.utils.group_roles import allows
import time
import uuid

METHODS={'paypal':('PayPal','EUR',15),'revolut':('Revolut','EUR',15),'usdt':('USDT TRC20','USDT',0)}

def cents(text):
    try: value=Decimal(text.strip().replace(',','.'))
    except InvalidOperation: raise ValueError('Envoie un montant comme 10 ou 10,50.')
    if not value.is_finite() or value<=0 or value>100000 or value.as_tuple().exponent < -2:
        raise ValueError('Montant positif, maximum 100000, avec deux décimales au plus.')
    return int(value*100)


def total(base,percent):
    return int((Decimal(base)*Decimal(100+percent)/100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))


def money(amount,currency): return f'{amount//100},{amount%100:02d} {currency}'


def btn(text,data): return {'text':text,'callback_data':data}



class ManualPayments:
    def payment_options(self,gid):
        return [p for p in self.db.all('SELECT * FROM payment_prices WHERE gid=? ORDER BY method',(gid,))
                if self.db.setting('pay:'+p['method']).strip() and p['cents']>0]

    def buy(self,uid,gid):
        g=self.group(gid)
        if not g or not g['enabled'] or not allows(g['role'],'prices') or self.user(uid)['banned'] or self.entitled(uid,g):
            return self.show(uid,'g:'+gid)
        order=self.db.one("SELECT * FROM manual_orders WHERE uid=? AND gid=? AND status IN ('awaiting_proof','review')",(uid,gid))
        if order: return self.payment_page(uid,order['id'])
        rows=[]
        for p in self.payment_options(gid):
            name,currency,fee=METHODS[p['method']]
            rows.append([btn(f'{name} — {money(total(p["cents"],fee),currency)}'+(' (+15 % inclus)' if fee else ''),f'method:{gid}:{p["method"]}')])
        rows.append([btn('⬅️ Retour','g:'+gid)])
        self.screen(uid,'<b>💳 Choisis ton moyen de paiement</b>\n\n'+('Les prix affichés incluent les suppléments.' if len(rows)>1 else 'Aucun moyen de paiement disponible pour le moment.'),rows)

    def payment_choose(self,uid,gid,method):
        if method not in METHODS: return
        g=self.group(gid)
        if not g or not g['enabled'] or not allows(g['role'],'prices') or self.user(uid)['banned'] or self.entitled(uid,g): return self.show(uid,'g:'+gid)
        with self.db.transaction():
            order=self.db.one("SELECT * FROM manual_orders WHERE uid=? AND gid=? AND status IN ('awaiting_proof','review')",(uid,gid))
            if not order:
                price=self.db.one('SELECT * FROM payment_prices WHERE gid=? AND method=?',(gid,method))
                destination=self.db.setting('pay:'+method)
                if not price or not destination: return
                name,currency,fee=METHODS[method]; oid=uuid.uuid4().hex
                self.db.run("INSERT INTO manual_orders(id,uid,gid,method,currency,base,fee,total,destination,monthly,status,created) VALUES (?,?,?,?,?,?,?,?,?,?,'awaiting_proof',?)",
                    (oid,uid,gid,method,currency,price['cents'],fee,total(price['cents'],fee),destination,g['monthly'] if g['role']=='vip' else 0,time.time()))
                order=self.db.one('SELECT * FROM manual_orders WHERE id=?',(oid,))
        self.payment_page(uid,order['id'])

    def payment_proof(self,uid,m,oid):
        o=self.db.one('SELECT * FROM manual_orders WHERE id=? AND uid=?',(oid,uid))
        if not o or o['status']!='awaiting_proof': return self.payment_page(uid,oid)
        media=m['photo'][-1] if m.get('photo') else m.get('document')
        if not media or (not m.get('photo') and media.get('mime_type') not in ('image/jpeg','image/png')):
            raise ValueError('Envoie une capture d’écran en photo, PNG ou JPEG.')
        if media.get('file_size',0)>10*1024*1024: raise ValueError('La preuve doit faire moins de 10 Mio.')
        unique=media['file_unique_id']
        if self.db.one('SELECT id FROM manual_orders WHERE proof_unique=?',(unique,)):
            raise ValueError('Cette preuve a déjà été utilisée. Contacte le support pour la faire vérifier.')
        kind='photo' if m.get('photo') else 'document'
        g=self.group(o['gid'])
        with self.db.transaction():
            self.db.run("UPDATE manual_orders SET status='review',proof_id=?,proof_unique=?,proof_kind=? WHERE id=? AND status='awaiting_proof'",(media['file_id'],unique,kind,oid))
            self.db.run('UPDATE users SET pending=NULL WHERE id=?',(uid,))
            for owner in self.owners:
                caption=f'Paiement à vérifier\nGroupe : {g["title"] if g else o["gid"]}\nUtilisateur : {uid}\n{METHODS[o["method"]][0]} : {money(o["total"],o["currency"])}\nRéférence : {oid}\nVérifier la réception réelle avant de valider.'
                self.enqueue('sendPhoto' if kind=='photo' else 'sendDocument',dict(chat_id=owner,**{kind:media['file_id']},caption=caption[:1024],reply_markup={'inline_keyboard':[[btn('🔎 Examiner','review:'+oid)]]}))
        self.payment_page(uid,oid)

    def payment_decide(self,uid,oid,approve):
        if uid not in self.owners: return
        with self.db.transaction():
            suffix='' if self.db.sqlite else ' FOR UPDATE'
            o=self.db.one('SELECT * FROM manual_orders WHERE id=?'+suffix,(oid,))
            if not o or o['status']!='review': return
            g=self.group(o['gid']); status='rejected'
            if approve:
                if not g or not g['enabled'] or not allows(g['role'],'prices') or self.user(o['uid'])['banned'] or self.entitled(o['uid'],g):
                    status='conflict'
                else:
                    self.grant(o['uid'],g,'payment',time.time()+30*86400 if o['monthly'] else None)
                    status='approved'
            self.db.run('UPDATE manual_orders SET status=?,reviewed_by=?,reviewed_at=? WHERE id=?',(status,uid,time.time(),oid))
            self.db.run('INSERT INTO audit(id,actor,action,created) VALUES (?,?,?,?)',(uuid.uuid4().hex,uid,'payment:'+oid+':'+status,time.time()))
            label={'approved':'✅ Ton paiement est validé. Ton accès est disponible.','rejected':'❌ Ta preuve de paiement a été refusée.','conflict':'Ton paiement nécessite une régularisation. Contacte le support.'}[status]
            self.enqueue('sendMessage',dict(chat_id=o['uid'],text=label,reply_markup={'inline_keyboard':[[btn('Voir ma demande','paystatus:'+oid)]]}))
        self.payment_review(uid,oid)

