import time

class LegacyPayments:
    def checkout(self,q):
        self.api.call('answerPreCheckoutQuery',pre_checkout_query_id=q['id'],ok=False,
                      error_message='Les paiements Stars sont désactivés. Reviens dans le menu du bot.')

    def paid(self,uid,p):
        o=self.db.one('SELECT * FROM orders WHERE id=?',(p['invoice_payload'],))
        if not o or o['uid']!=uid or p['currency']!='XTR' or p['total_amount']!=o['amount']:
            self.alert('Paiement incohérent : vérification nécessaire.'); return
        if o['status']=='paid': return
        g=self.group(o['gid'])
        with self.db.transaction():
            self.db.run("UPDATE orders SET status='paid',charge=? WHERE id=?",(p['telegram_payment_charge_id'],o['id']))
            # Entitlement obtained while the payment was in flight: refund instead of double sale.
            if not g or not g['enabled'] or self.user(uid)['banned'] or self.entitled(uid,g):
                self.enqueue('refundStarPayment',dict(user_id=uid,telegram_payment_charge_id=p['telegram_payment_charge_id']))
            else:
                self.grant(uid,g,'payment',time.time()+30*86400 if o['monthly'] else None)
        self.show(uid,'g:'+o['gid'])

