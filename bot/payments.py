"""Manual external payment review. Amounts are stored as integer cents, never floats."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import html
import json
import time
import uuid

METHODS={'paypal':('PayPal','EUR',15),'revolut':('Revolut','EUR',15),'usdt':('USDT TRC20','USDT',0)}

SCHEMA='''
CREATE TABLE IF NOT EXISTS payment_prices (gid TEXT NOT NULL, method TEXT NOT NULL, cents INTEGER NOT NULL, PRIMARY KEY(gid,method));
CREATE TABLE IF NOT EXISTS manual_orders (id TEXT PRIMARY KEY, uid BIGINT NOT NULL, gid TEXT NOT NULL, method TEXT NOT NULL, currency TEXT NOT NULL, base INTEGER NOT NULL, fee INTEGER NOT NULL, total INTEGER NOT NULL, destination TEXT NOT NULL, monthly INTEGER NOT NULL, status TEXT NOT NULL, created DOUBLE PRECISION NOT NULL, proof_id TEXT, proof_unique TEXT, proof_kind TEXT, reviewed_by BIGINT, reviewed_at DOUBLE PRECISION);
CREATE UNIQUE INDEX IF NOT EXISTS manual_pending ON manual_orders(uid,gid) WHERE status IN ('awaiting_proof','review');
CREATE UNIQUE INDEX IF NOT EXISTS manual_unique_proof ON manual_orders(proof_unique) WHERE proof_unique IS NOT NULL;
'''

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
    def payment_setup(self):
        for sql in SCHEMA.split(';'):
            if sql.strip(): self.db.run(sql)

    def payment_options(self,gid):
        return [p for p in self.db.all('SELECT * FROM payment_prices WHERE gid=? ORDER BY method',(gid,))
                if self.db.setting('pay:'+p['method']).strip() and p['cents']>0]

    def buy(self,uid,gid):
        g=self.group(gid)
        if not g or not g['enabled'] or self.user(uid)['banned'] or self.entitled(uid,g):
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
        if not g or not g['enabled'] or self.user(uid)['banned'] or self.entitled(uid,g): return self.show(uid,'g:'+gid)
        with self.db.transaction():
            order=self.db.one("SELECT * FROM manual_orders WHERE uid=? AND gid=? AND status IN ('awaiting_proof','review')",(uid,gid))
            if not order:
                price=self.db.one('SELECT * FROM payment_prices WHERE gid=? AND method=?',(gid,method))
                destination=self.db.setting('pay:'+method)
                if not price or not destination: return
                name,currency,fee=METHODS[method]; oid=uuid.uuid4().hex
                self.db.run("INSERT INTO manual_orders(id,uid,gid,method,currency,base,fee,total,destination,monthly,status,created) VALUES (?,?,?,?,?,?,?,?,?,?,'awaiting_proof',?)",
                    (oid,uid,gid,method,currency,price['cents'],fee,total(price['cents'],fee),destination,g['monthly'],time.time()))
                order=self.db.one('SELECT * FROM manual_orders WHERE id=?',(oid,))
        self.payment_page(uid,order['id'])

    def payment_page(self,uid,oid):
        o=self.db.one('SELECT * FROM manual_orders WHERE id=? AND uid=?',(oid,uid))
        if not o: return self.show(uid)
        rows=[]
        text=f'<b>💳 {METHODS[o["method"]][0]}</b>\nRéférence : <code>{o["id"]}</code>\n\n'
        if o['status']=='awaiting_proof':
            text+=f'Base : {money(o["base"],o["currency"])}\nSupplément : {o["fee"]} %\n<b>Total : {money(o["total"],o["currency"])}</b>\n\n'
            text+='Destinataire :\n'+html.escape(o['destination'])+'\n\n'
            if o['method']=='usdt': text+='Réseau : <b>TRC20 uniquement</b>. Le montant USDT est fixé par l’administrateur.\n\n'
            text+='Effectue le paiement, puis envoie une capture avec le bouton ci-dessous.'
            rows=[[btn('📎 Envoyer ma preuve','proofpay:'+oid)], [btn('Annuler — je n’ai pas payé','cancelpay:'+oid)]]
        elif o['status']=='review': text+='⏳ Preuve reçue. Un administrateur vérifie ton paiement.'
        elif o['status']=='approved': text+='✅ Paiement validé.'; rows=[[btn('🚪 Mon accès','g:'+o['gid'])]]
        elif o['status']=='rejected': text+='❌ Paiement refusé. Contacte le support si tu as effectué le transfert.'
        elif o['status']=='conflict': text+='Ton paiement doit être régularisé par le support : ton accès ou le groupe a changé.'
        else: text+='Demande annulée.'
        rows += [[btn('🔄 Actualiser','paystatus:'+oid),btn('⬅️ Retour','g:'+o['gid'])]]
        self.screen(uid,text,rows)

    def payment_proof(self,uid,m,oid):
        o=self.db.one('SELECT * FROM manual_orders WHERE id=? AND uid=?',(oid,uid))
        if not o or o['status']!='awaiting_proof': return self.payment_page(uid,oid)
        media=m['photo'][-1] if m.get('photo') else m.get('document')
        if not media or (not m.get('photo') and media.get('mime_type') not in ('image/jpeg','image/png','application/pdf')):
            raise ValueError('Envoie une capture en photo, PNG, JPEG ou PDF.')
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

    def payment_review(self,uid,oid):
        if uid not in self.owners: return
        o=self.db.one('SELECT * FROM manual_orders WHERE id=?',(oid,))
        if not o: return
        text=f'<b>Vérification de paiement</b>\n\nUtilisateur : {o["uid"]}\nRéférence : {oid}\nMoyen : {METHODS[o["method"]][0]}\nTotal : {money(o["total"],o["currency"])}\nDestinataire : {html.escape(o["destination"])}\nÉtat : {o["status"]}\n\nVérifie la réception du transfert dans ton compte avant de valider.'
        rows=[]
        if o['status']=='review': rows=[[btn('✅ Paiement reçu','approvepay:'+oid),btn('❌ Refuser','rejectpay:'+oid)]]
        rows += [[btn('⬅️ Paiements','payments')]]
        self.screen(uid,text,rows)

    def payment_decide(self,uid,oid,approve):
        if uid not in self.owners: return
        with self.db.transaction():
            suffix='' if self.db.sqlite else ' FOR UPDATE'
            o=self.db.one('SELECT * FROM manual_orders WHERE id=?'+suffix,(oid,))
            if not o or o['status']!='review': return
            g=self.group(o['gid']); status='rejected'
            if approve:
                if not g or not g['enabled'] or self.user(o['uid'])['banned'] or self.entitled(o['uid'],g):
                    status='conflict'
                else:
                    self.grant(o['uid'],g,'payment',time.time()+30*86400 if o['monthly'] else None)
                    status='approved'
            self.db.run('UPDATE manual_orders SET status=?,reviewed_by=?,reviewed_at=? WHERE id=?',(status,uid,time.time(),oid))
            self.db.run('INSERT INTO audit(id,actor,action,created) VALUES (?,?,?,?)',(uuid.uuid4().hex,uid,'payment:'+oid+':'+status,time.time()))
            label={'approved':'✅ Ton paiement est validé. Ton accès est disponible.','rejected':'❌ Ta preuve de paiement a été refusée.','conflict':'Ton paiement nécessite une régularisation. Contacte le support.'}[status]
            self.enqueue('sendMessage',dict(chat_id=o['uid'],text=label,reply_markup={'inline_keyboard':[[btn('Voir ma demande','paystatus:'+oid)]]}))
        self.payment_review(uid,oid)

    def payment_callback(self,uid,data):
        parts=data.split(':'); action=parts[0]
        if action=='method': self.payment_choose(uid,parts[1],parts[2])
        elif action=='paystatus': self.payment_page(uid,parts[1])
        elif action=='proofpay':
            o=self.db.one("SELECT id FROM manual_orders WHERE id=? AND uid=? AND status='awaiting_proof'",(parts[1],uid))
            if o:
                self.db.run('UPDATE users SET pending=? WHERE id=?',('paymentproof:'+parts[1],uid))
                self.screen(uid,'📎 Envoie maintenant ta capture ou ton PDF.',[[btn('⬅️ Retour','paystatus:'+parts[1])]])
        elif action=='cancelpay':
            self.db.run("UPDATE manual_orders SET status='cancelled' WHERE id=? AND uid=? AND status='awaiting_proof'",(parts[1],uid))
            self.payment_page(uid,parts[1])
        elif action=='review': self.payment_review(uid,parts[1])
        elif action in ('approvepay','rejectpay'): self.payment_decide(uid,parts[1],action=='approvepay')
        elif action=='payments' and uid in self.owners:
            orders=self.db.all("SELECT * FROM manual_orders WHERE status IN ('review','conflict') ORDER BY created LIMIT 50")
            self.screen(uid,'<b>💳 Paiements à vérifier</b>',[[btn(f'{o["uid"]} · {money(o["total"],o["currency"])} · {o["status"]}','review:'+o['id'])] for o in orders]+[[btn('⚙️ Coordonnées de paiement','payconfig')],[btn('⬅️ Administration','admin')]])
        elif action=='payconfig' and uid in self.owners:
            self.screen(uid,'<b>Coordonnées de paiement</b>\nConfigure les comptes et l’adresse USDT.',[[btn(name,'paydest:'+method)] for method,(name,_,_) in METHODS.items()]+[[btn('⬅️ Administration','admin')]])
        elif action=='paydest' and uid in self.owners and parts[1] in METHODS:
            self.db.run('UPDATE users SET pending=? WHERE id=?',(data,uid))
            self.screen(uid,'Envoie les coordonnées de réception. Un tiret désactive ce moyen.',[[btn('⬅️ Annuler','payconfig')]])
        elif action=='prices':
            g=self.group(parts[1])
            if not g or not self.admin(uid,g): return True
            rows=[]
            for method,(name,currency,fee) in METHODS.items():
                p=self.db.one('SELECT cents FROM payment_prices WHERE gid=? AND method=?',(g['id'],method))
                rows.append([btn(name+' : '+(money(p['cents'],currency) if p else 'non configuré'),f'price:{g["id"]}:{method}')])
            self.screen(uid,'<b>Prix de base</b>\nLes 15 % sont ajoutés automatiquement pour PayPal et Revolut. USDT possède son propre montant.',rows+[[btn('⬅️ Retour','a:'+g['id'])]])
        elif action=='price':
            g=self.group(parts[1])
            if not g or not self.admin(uid,g) or parts[2] not in METHODS: return True
            self.db.run('UPDATE users SET pending=? WHERE id=?',(data,uid))
            self.screen(uid,'Envoie le prix de base, par exemple 10,50. Un tiret désactive ce tarif.',[[btn('⬅️ Annuler','prices:'+g['id'])]])
        else: return False
        return True

    def payment_input(self,uid,m,pending):
        parts=pending.split(':'); action=parts[0]; text=m.get('text','').strip()
        if action=='paymentproof': self.payment_proof(uid,m,parts[1]); return True
        if action=='paydest' and uid in self.owners:
            if len(text)>1000 or not text: raise ValueError('Coordonnées requises, maximum 1000 caractères.')
            self.db.set('pay:'+parts[1],'' if text=='-' else text)
            self.db.run('UPDATE users SET pending=NULL WHERE id=?',(uid,))
            self.payment_callback(uid,'payconfig'); return True
        if action=='price':
            g=self.group(parts[1])
            if not g or not self.admin(uid,g): return True
            method=parts[2]
            if text=='-': self.db.run('DELETE FROM payment_prices WHERE gid=? AND method=?',(g['id'],method))
            else:
                amount=cents(text)
                self.db.run('INSERT INTO payment_prices(gid,method,cents) VALUES (?,?,?) ON CONFLICT(gid,method) DO UPDATE SET cents=excluded.cents',(g['id'],method,amount))
            self.db.run('UPDATE users SET pending=NULL WHERE id=?',(uid,))
            self.payment_callback(uid,'prices:'+g['id']); return True
        return False
