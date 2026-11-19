from app.keyboards.inline import button

class Trials:
    def proof_minutes(self,g):
        return max(1,min(1440,int(self.db.setting('proof_minutes:'+g['id'],'10'))))

    def proof_eligible(self,uid,g):
        return self.db.setting('proof_invite:'+g['id'],'0')!='1' or bool(self.db.one('SELECT 1 FROM referrals WHERE inviter=? AND valid=1',(uid,)))

    def trial_callback(self,uid,data):
        if not data.startswith('trial:'):return False
        parts=data.split(':');g=self.group(parts[1]) if len(parts)>1 else None
        if not g or g['role']!='proof' or not self.admin(uid,g,fresh=True):return True
        if len(parts)==3:
            if parts[2]=='invite':self.db.set('proof_invite:'+g['id'],'0' if self.db.setting('proof_invite:'+g['id'],'0')=='1' else '1')
            elif parts[2] in ('5','10','15','30','60','120','240','1440'):self.db.set('proof_minutes:'+g['id'],parts[2])
        rows=[[button(str(n)+' min','trial:'+g['id']+':'+str(n)) for n in values] for values in ((5,10,15),(30,60,120),(240,1440))]
        state='ON' if self.db.setting('proof_invite:'+g['id'],'0')=='1' else 'OFF'
        rows += [[button('Invitation préalable : '+state,'trial:'+g['id']+':invite')],[button('⬅️ Retour','a:'+g['id'])]]
        self.screen(uid,'<b>👀 Visite découverte</b>\nDurée : '+str(self.proof_minutes(g))+' minutes à partir de l’entrée.\nUne visite par personne.\n\nInvitation préalable ON : au moins une personne réellement invitée avec une invitation validée dans le réseau. Partager un lien seul ne suffit pas.',rows)
        return True
