import html
import json
import time
import uuid
from app.keyboards.inline import button,keyboard
from app.services.telegram import APIError
from app.services.monitoring import safe_error
from app.utils.core import active_access

class Replacement:
    def replace_group(self,uid,g,value):
        if uid not in self.owners:raise ValueError('Seul le propriétaire du réseau peut remplacer un groupe.')
        if value>=0:raise ValueError('Choisis un supergroupe avec un identifiant Telegram négatif.')
        if value==g['chat']:raise ValueError('Ce groupe est déjà la destination actuelle.')
        candidate={**g,'chat':value}
        self.check_rights(candidate)
        registered=self.db.one('SELECT * FROM groups WHERE chat=?',(value,))
        if registered and (registered['enabled'] or registered['role']!='unset'):
            raise ValueError('Ce groupe appartient déjà à une configuration. Utilise un nouveau groupe.')
        with self.db.transaction():
            rows=self.db.all('SELECT * FROM groups WHERE id=? OR chat=? ORDER BY id'+('' if self.db.sqlite else ' FOR UPDATE'),(g['id'],value))
            current=next((row for row in rows if row['id']==g['id']),None)
            registered=next((row for row in rows if row['chat']==value),None)
            if not current or current['chat']!=g['chat']:raise ValueError('La destination a changé. Ouvre à nouveau le remplacement.')
            if registered and (registered['enabled'] or registered['role']!='unset'):raise ValueError('Le groupe choisi est maintenant configuré ; son contenu ne sera pas écrasé.')
            if registered: self.db.run('DELETE FROM groups WHERE id=?',(registered['id'],))
            pause=self.db.one('SELECT * FROM group_pauses WHERE chat=?',(g['chat'],))
            self.db.run('DELETE FROM group_pauses WHERE chat=?',(value,))
            if g['role']=='portal':self.prepare_portal_move(g,value)
            self.db.run('UPDATE groups SET chat=?,opened=0,enabled=? WHERE id=?',(value,pause['was_enabled'] if pause else g['enabled'],g['id']))
            self.db.set('portal_error:'+g['id'],'')
            self.db.set('locked:'+g['id'],'')
            self.db.run('DELETE FROM notices WHERE gid=?',(g['id'],))
            self.db.run('DELETE FROM media_tasks WHERE gid=?',(g['id'],))
            for link in self.db.all('SELECT url FROM links WHERE gid=? AND expires!=0',(g['id'],)):
                self.enqueue('revokeChatInviteLink',dict(chat_id=g['chat'],invite_link=link['url']))
            self.db.run('DELETE FROM links WHERE gid=?',(g['id'],))
            self.db.run('UPDATE members SET present=0 WHERE gid=?',(g['id'],))
            for a in self.db.all('SELECT * FROM access WHERE gid=? AND revoked=0',(g['id'],)):
                if active_access(a,time.time()):
                    self.enqueue('sendMessage',dict(chat_id=a['uid'],text=self.tr(a['uid'],'Ton groupe a été remplacé. Ton accès est conservé.'),reply_markup=keyboard([button(self.tr(a['uid'],'🚪 Retrouver mon accès'),url=self.deep('g_'+g['id']))])))
            self.db.run('INSERT INTO audit(id,actor,action,created) VALUES (?,?,?,?)',(uuid.uuid4().hex,uid,'replace:'+g['id']+':'+str(g['chat'])+':'+str(value),time.time()))
        return self.group(g['id'])

    def replacement_picker(self,uid,gid,page=0):
        if uid not in self.owners:return
        g=self.group(gid)
        if not g:return self.show(uid,'admin')
        self.db.run('DELETE FROM settings WHERE key=?',('replacement:'+str(uid),))
        candidates=self.db.all("SELECT * FROM groups WHERE role='unset' AND enabled=0 AND id!=? ORDER BY title,id",(gid,))
        pages=max(1,(len(candidates)+7)//8);page=max(0,min(page,pages-1))
        rows=[[button(c['title']+' · '+str(c['chat']),'replacepick:'+gid+':'+c['id'])] for c in candidates[page*8:page*8+8]]
        text='<b>🔁 Remplacer '+html.escape(g['title'])+'</b>\n\nChoisis le nouveau groupe. Seuls les groupes détectés sans rôle et désactivés sont proposés. Les droits du bot seront vérifiés avant le remplacement.\n\nLa configuration de l’ancien groupe sera conservée ; la fiche provisoire du nouveau sera fusionnée.'
        if not candidates:text+='\n\nAucun groupe disponible. Ajoute le bot comme administrateur au nouveau supergroupe privé, sans lui attribuer de rôle ici, puis actualise.'
        if pages>1:rows.append([button('◀️',f'replacelist:{gid}:{max(0,page-1)}'),button(f'{page+1}/{pages} ▶️',f'replacelist:{gid}:{min(pages-1,page+1)}')])
        rows += [[button('🔄 Actualiser','replacelist:'+gid)],[button('⬅️ Annuler','replacecancel:'+gid)]]
        return self.screen(uid,text,rows)

    def replacement_callback(self,uid,data):
        parts=data.split(':');action=parts[0]
        legacy=action=='edit' and len(parts)==3 and parts[2]=='chat'
        if not legacy and action not in ('replacelist','replacepick','replaceconfirm','replacecancel'):return False
        if uid not in self.owners:return True
        if action=='replacecancel' and len(parts)==2:
            self.db.run('DELETE FROM settings WHERE key=?',('replacement:'+str(uid),))
            self.show(uid,'a:'+parts[1]);return True
        if legacy or action=='replacelist':
            try:page=int(parts[2]) if not legacy and len(parts)>2 else 0
            except ValueError:page=0
            self.replacement_picker(uid,parts[1],page);return True
        if action=='replacepick' and len(parts)==3:
            g=self.group(parts[1]);target=self.group(parts[2])
            if not g or not target or target['id']==g['id'] or target['role']!='unset' or target['enabled']:
                self.screen(uid,'Ce groupe n’est plus disponible pour le remplacement.',[[button('🔄 Choisir à nouveau','replacelist:'+parts[1])]]);return True
            token=uuid.uuid4().hex[:12]
            plan={'token':token,'gid':g['id'],'old_chat':g['chat'],'target':target['id'],'chat':target['chat'],'at':time.time()}
            self.db.set('replacement:'+str(uid),json.dumps(plan))
            text='<b>Confirmer le remplacement ?</b>\n\nConfiguration conservée : '+html.escape(g['title'])+'\nNouveau groupe Telegram : '+html.escape(target['title'])+'\nIdentifiant : '+str(target['chat'])+'\n\nLes réglages, invitations validées et accès restent conservés. La fiche sans rôle du nouveau groupe sera fusionnée.'
            if g['role']=='portal':text+=' Les nouveaux liens et notifications seront préparés en arrière-plan.'
            self.screen(uid,text,[[button('✅ Confirmer le remplacement','replaceconfirm:'+token)],[button('⬅️ Autre groupe','replacelist:'+g['id'])]])
            return True
        if action=='replaceconfirm' and len(parts)==2:
            plan=json.loads(self.db.setting('replacement:'+str(uid),'{}'))
            if plan.get('token')!=parts[1]:return True
            if time.time()-plan.get('at',0)>600:
                self.replacement_picker(uid,plan['gid']);return True
            g=self.group(plan['gid']);target=self.group(plan['target'])
            if not g or g['chat']!=plan['old_chat'] or not target or target['chat']!=plan['chat'] or target['role']!='unset' or target['enabled']:
                self.db.run('DELETE FROM settings WHERE key=?',('replacement:'+str(uid),))
                self.screen(uid,'La configuration a changé. Choisis à nouveau le groupe.',[[button('🔄 Choisir','replacelist:'+plan['gid'])]]);return True
            try:updated=self.replace_group(uid,g,target['chat'])
            except (ValueError,APIError) as error:
                self.record_failure('replacement',error)
                self.screen(uid,'⚠️ Remplacement non effectué.\n'+html.escape(safe_error(error))+'\nVérifie que le nouveau groupe est privé et que le bot peut supprimer, restreindre et inviter.',[[button('🔄 Réessayer','replaceconfirm:'+parts[1])],[button('⬅️ Autre groupe','replacelist:'+g['id'])]])
                return True
            self.db.run('DELETE FROM settings WHERE key=?',('replacement:'+str(uid),))
            self.db.run('UPDATE users SET pending=NULL WHERE id=?',(uid,))
            self.record_success('replacement')
            self.screen(uid,'✅ <b>Remplacement effectué</b>\n\n'+html.escape(g['title'])+' utilise maintenant '+html.escape(target['title'])+' ('+str(updated['chat'])+').\nLes réglages et les accès sont conservés.'+('\nLes nouveaux liens et les notifications sont en préparation.' if g['role']=='portal' else ''),[[button('⚙️ Ouvrir la configuration','a:'+g['id'])],[button('🩺 Suivre le réseau','health')]])
            return True
        return True
