import html
from app.services.payments import METHODS, money, cents, btn

class PaymentScreens:
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

    def payment_review(self,uid,oid):
        if uid not in self.owners: return
        o=self.db.one('SELECT * FROM manual_orders WHERE id=?',(oid,))
        if not o: return
        text=f'<b>Vérification de paiement</b>\n\nUtilisateur : {o["uid"]}\nRéférence : {oid}\nMoyen : {METHODS[o["method"]][0]}\nTotal : {money(o["total"],o["currency"])}\nDestinataire : {html.escape(o["destination"])}\nÉtat : {o["status"]}\n\nVérifie la réception du transfert dans ton compte avant de valider.'
        rows=[]
        if o['status']=='review': rows=[[btn('✅ Paiement reçu','approvepay:'+oid),btn('❌ Refuser','rejectpay:'+oid)]]
        rows += [[btn('⬅️ Paiements','payments')]]
        self.screen(uid,text,rows)

    def payment_callback(self,uid,data):
        parts=data.split(':'); action=parts[0]
        if action=='method': self.payment_choose(uid,parts[1],parts[2])
        elif action=='paystatus': self.payment_page(uid,parts[1])
        elif action=='proofpay':
            o=self.db.one("SELECT id FROM manual_orders WHERE id=? AND uid=? AND status='awaiting_proof'",(parts[1],uid))
            if o:
                self.db.run('UPDATE users SET pending=? WHERE id=?',('paymentproof:'+parts[1],uid))
                self.screen(uid,'📎 Envoie maintenant ta capture d’écran.',[[btn('⬅️ Retour','paystatus:'+parts[1])]])
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

