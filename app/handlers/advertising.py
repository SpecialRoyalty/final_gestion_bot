import html
import json
import uuid
from app.keyboards.inline import button
from app.utils.group_roles import allows

class Advertising:
    def advertising_callback(self,uid,data):
        parts=data.split(':');action=parts[0]
        if action not in ('ads','adnew','adfrequency','adconfirm','adoff'):return False
        if len(parts)<2:return True
        g=self.group(parts[1])
        if not g or not self.admin(uid,g,fresh=True) or not allows(g['role'],'ads'):return True
        key=f'ad_draft:{uid}:{g["id"]}'
        if action=='ads':
            self.db.set(key,'');self.advertising_list(uid,g)
        elif action=='adnew':
            self.db.set(key,'')
            self.screen(uid,'<b>📣 Nouvelle publicité · 1/3</b>\nChoisis la fréquence. Le portail diffuse en continu ; les autres groupes diffusent pendant leur ouverture.',
                [[button('Toutes les '+str(h)+' heures',f'adfrequency:{g["id"]}:{h}') for h in (2,4)],
                 [button('Toutes les '+str(h)+' heures',f'adfrequency:{g["id"]}:{h}') for h in (6,12)],
                 [button('Annuler','ads:'+g['id'])]])
        elif action=='adfrequency' and len(parts)==3 and parts[2] in ('2','4','6','12'):
            self.db.set(key,json.dumps({'id':uuid.uuid4().hex,'hours':int(parts[2])}))
            self.db.run('UPDATE users SET pending=? WHERE id=?',('adnew:'+g['id'],uid))
            self.screen(uid,'<b>📣 Nouvelle publicité · 2/3</b>\nEnvoie le texte, ou une photo avec sa légende (800 caractères maximum). Tu verras un aperçu avant de confirmer.',[[button('⬅️ Fréquence','adnew:'+g['id']),button('Annuler','ads:'+g['id'])]])
        elif action=='adconfirm' and len(parts)==3:
            raw=self.db.setting(key);draft=json.loads(raw) if raw else {}
            if draft.get('id')==parts[2] and 'body' in draft:
                self.db.run('INSERT INTO ads(id,gid,text,photo,interval_hours) VALUES (?,?,?,?,?) ON CONFLICT(id) DO NOTHING',(draft['id'],g['id'],draft['body'],draft['photo'],draft['hours']))
                self.db.set(key,'')
            self.advertising_list(uid,g)
        elif action=='adoff' and len(parts)==3:
            self.db.run('UPDATE ads SET enabled=1-enabled WHERE id=? AND gid=?',(parts[2],g['id']))
            self.advertising_list(uid,g)
        return True

    def advertising_list(self,uid,g):
        ads=self.db.all('SELECT * FROM ads WHERE gid=? ORDER BY id',(g['id'],))
        rows=[[button(('🟢 ' if a['enabled'] else '⏸ ')+a['text'][:25]+' · '+str(a['interval_hours'])+' h','adoff:'+g['id']+':'+a['id'])] for a in ads]
        self.screen(uid,'<b>📣 Publicités en rotation</b>\nUne seule publicité reste affichée. Chaque nouvelle diffusion supprime la précédente. Sélectionne une publicité pour la mettre en pause ou la réactiver.',rows+[[button('➕ Ajouter','adnew:'+g['id'])],[button('⬅️ Retour','a:'+g['id'])]])

    def advertising_input(self,uid,m,pending):
        if not pending.startswith('adnew:'):return False
        g=self.group(pending.split(':',1)[1])
        if not g or not self.admin(uid,g,fresh=True) or not allows(g['role'],'ads'):return True
        key=f'ad_draft:{uid}:{g["id"]}';raw=self.db.setting(key);draft=json.loads(raw) if raw else {}
        if 'hours' not in draft:raise ValueError('Choisis d’abord la fréquence avec Ajouter une publicité.')
        body=m.get('text',m.get('caption','')).strip()
        if not body or len(body.encode('utf-16-le'))//2>800:raise ValueError('Ajoute un texte ou une légende de 1 à 800 caractères.')
        if m.get('document') or m.get('video'):raise ValueError('Envoie un texte ou une photo Telegram légendée.')
        draft.update(body=body,photo=m['photo'][-1]['file_id'] if m.get('photo') else None)
        self.db.set(key,json.dumps(draft));self.db.run('UPDATE users SET pending=NULL WHERE id=?',(uid,))
        self.refresh_panel(uid,lambda:self.screen(uid,'<b>📣 Aperçu · 3/3</b>\nToutes les '+str(draft['hours'])+' heures.\n\n'+html.escape(body),
            [[button('✅ Enregistrer','adconfirm:'+g['id']+':'+draft['id'])],
             [button('✏️ Modifier','adfrequency:'+g['id']+':'+str(draft['hours'])),button('Annuler','ads:'+g['id'])]],photo=draft['photo']))
        return True
