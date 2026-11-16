import html
from app.utils.group_roles import allows
from app.services.payments import METHODS, money, cents, btn

class PaymentScreens:
    def payment_page(self,uid,oid):
        o=self.db.one('SELECT * FROM manual_orders WHERE id=? AND uid=?',(oid,uid))
        if not o: return self.show(uid)
        rows=[]
        text='<b>💳 '+METHODS[o['method']][0]+'</b>\n'+self.tr(uid,'Référence : ')+'<code>'+o['id']+'</code>\n\n'
        if o['status']=='awaiting_proof':
            text+=self.tr(uid,'Base : ')+money(o['base'],o['currency'])+'\n'+self.tr(uid,'Supplément : ')+str(o['fee'])+' %\n<b>'+self.tr(uid,'Total : ')+money(o['total'],o['currency'])+'</b>\n\n'
            text+=self.tr(uid,'Destinataire :\n')+html.escape(o['destination'])+'\n\n'
            if o['method']=='usdt': text+=self.tr(uid,'Réseau : <b>TRC20 uniquement</b>. Le montant USDT est fixé par l’administrateur.\n\n')
            text+=self.tr(uid,'Effectue le paiement, puis envoie une capture avec le bouton ci-dessous.')
            rows=[[btn(self.tr(uid,'📎 Envoyer ma preuve'),'proofpay:'+oid)], [btn(self.tr(uid,'Annuler — je n’ai pas payé'),'cancelpay:'+oid)]]
        elif o['status']=='review': text+=self.tr(uid,'⏳ Preuve reçue. Un administrateur vérifie ton paiement.')
        elif o['status']=='approved': text+=self.tr(uid,'✅ Paiement validé.'); rows=[[btn(self.tr(uid,'🚪 Mon accès'),'g:'+o['gid'])]]
        elif o['status']=='rejected': text+=self.tr(uid,'❌ Paiement refusé. Contacte le support si tu as effectué le transfert.')
        elif o['status']=='conflict': text+=self.tr(uid,'Ton paiement doit être régularisé par le support : ton accès ou le groupe a changé.')
        else: text+=self.tr(uid,'Demande annulée.')
        rows += [[btn(self.tr(uid,'🔄 Actualiser'),'paystatus:'+oid),btn(self.tr(uid,'⬅️ Retour'),'g:'+o['gid'])]]
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
        if action=='paydisable' and uid in self.owners and len(parts)==2 and parts[1] in METHODS:
            self.db.set('pay:'+parts[1],''); self.payment_callback(uid,'payconfig')
        elif action=='pricedisable' and len(parts)==3:
            g=self.group(parts[1])
            if g and self.admin(uid,g) and allows(g['role'],'prices') and parts[2] in METHODS:
                self.db.run('DELETE FROM payment_prices WHERE gid=? AND method=?',(g['id'],parts[2]))
                self.payment_callback(uid,'prices:'+g['id'])
        elif action=='method': self.payment_choose(uid,parts[1],parts[2])
        elif action=='paystatus': self.payment_page(uid,parts[1])
        elif action=='proofpay':
            o=self.db.one("SELECT id FROM manual_orders WHERE id=? AND uid=? AND status='awaiting_proof'",(parts[1],uid))
            if o:
                self.db.run('UPDATE users SET pending=? WHERE id=?',('paymentproof:'+parts[1],uid))
                self.screen(uid,self.tr(uid,'📎 Envoie maintenant ta capture d’écran.'),[[btn(self.tr(uid,'⬅️ Retour'),'paystatus:'+parts[1])]])
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
            self.screen(uid,'Envoie les coordonnées de réception, ou désactive ce moyen avec le bouton ci-dessous.',[[btn('⏸ Désactiver ce moyen','paydisable:'+parts[1])],[btn('⬅️ Annuler','payconfig')]])
        elif action=='prices':
            g=self.group(parts[1])
            if not g or not self.admin(uid,g) or not allows(g['role'],'prices'): return True
            rows=[]
            for method,(name,currency,fee) in METHODS.items():
                p=self.db.one('SELECT cents FROM payment_prices WHERE gid=? AND method=?',(g['id'],method))
                rows.append([btn(name+' : '+(money(p['cents'],currency) if p else 'non configuré'),f'price:{g["id"]}:{method}')])
            self.screen(uid,'<b>Prix de base</b>\nLes 15 % sont ajoutés automatiquement pour PayPal et Revolut. USDT possède son propre montant.',rows+[[btn(self.tr(uid,'⬅️ Retour'),'a:'+g['id'])]])
        elif action=='price':
            g=self.group(parts[1])
            if not g or not self.admin(uid,g) or not allows(g['role'],'prices') or parts[2] not in METHODS: return True
            self.db.run('UPDATE users SET pending=? WHERE id=?',(data,uid))
            self.screen(uid,'Envoie le prix de base, par exemple 10,50, ou retire ce tarif avec le bouton ci-dessous.',[[btn('🗑 Retirer ce tarif','pricedisable:'+g['id']+':'+parts[2])],[btn('⬅️ Annuler','prices:'+g['id'])]])
        else: return False
        return True

    def payment_input(self,uid,m,pending):
        parts=pending.split(':'); action=parts[0]; text=m.get('text','').strip()
        if action=='paymentproof': self.payment_proof(uid,m,parts[1]); return True
        if action=='paydest' and uid in self.owners:
            if text=='-': raise ValueError('Utilise le bouton Désactiver ce moyen pour le retirer.')
            if len(text)>1000 or not text: raise ValueError('Coordonnées requises, maximum 1000 caractères.')
            self.db.set('pay:'+parts[1],text)
            self.db.run('UPDATE users SET pending=NULL WHERE id=?',(uid,))
            self.payment_callback(uid,'payconfig'); return True
        if action=='price':
            g=self.group(parts[1])
            if not g or not self.admin(uid,g) or not allows(g['role'],'prices'): return True
            method=parts[2]
            amount=cents(text)
            self.db.run('INSERT INTO payment_prices(gid,method,cents) VALUES (?,?,?) ON CONFLICT(gid,method) DO UPDATE SET cents=excluded.cents',(g['id'],method,amount))
            self.db.run('UPDATE users SET pending=NULL WHERE id=?',(uid,))
            self.payment_callback(uid,'prices:'+g['id']); return True
        return False

