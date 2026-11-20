import html
from app.services.telegram import APIError
import time
import uuid
from app.utils.core import ROLES, SLOTS, active_access
from app.utils.group_roles import allows, ROLE_LABELS
from app.keyboards.inline import button, keyboard

class Callbacks:
    def show_referral_settings(self,uid):
        if uid not in self.owners: return
        current=self.db.setting('referral_rule','simple')
        text=('<b>🔗 Validation des invitations</b>\n\n'
              '<b>Simple</b> — l’invité reste au moins 5 minutes dans le portail.\n\n'
              '<b>Cascade</b> — l’invité reste au moins 5 minutes et invite lui-même une personne restée au moins 5 minutes.\n\n'
              'Ces critères sont internes : ils ne sont pas affichés aux membres. Les invitations déjà validées restent acquises.')
        rows=[[button(('✅ ' if current=='simple' else '')+'Simple','referralrule:simple'),
               button(('✅ ' if current=='cascade' else '')+'Cascade','referralrule:cascade')],
              [button('⬅️ Administration','admin')]]
        self.screen(uid,text,rows)

    def callback(self,q):
        uid=q['from']['id']; data=q.get('data','home')
        self.answer_callback(q)
        if q.get('message',{}).get('chat',{}).get('id')!=uid: return
        self.db.set('dm_blocked:'+str(uid),'')
        if self.user(uid)['permanent']: return self.show(uid)
        if data.startswith('launchack:'): return self.acknowledge_launch(uid,data.split(':',1)[1])
        if self.launch_gate(uid): return
        self.db.run('UPDATE users SET pending=NULL WHERE id=?',(uid,))
        if data.startswith('daypick:'):
            parts=data.split(':');g=self.group(parts[1])
            if len(parts)==3 and g and self.admin(uid,g,fresh=True) and allows(g['role'],'weekday') and parts[2] in tuple(map(str,range(7))):
                self.db.run('UPDATE groups SET weekday=? WHERE id=?',(int(parts[2]),g['id']))
                return self.show(uid,'a:'+g['id'])
            return
        if self.replacement_callback(uid,data):return
        if self.trial_callback(uid,data):return
        if self.advertising_callback(uid,data): return
        if self.workflow_callback(uid,data): return
        if self.welcome_callback(uid,data): return
        if self.readiness_callback(uid,data): return
        if self.monitoring_callback(uid,data): return
        if self.payment_callback(uid,data): return
        parts=data.split(':'); action=parts[0]
        if action=='referralrule' and uid in self.owners:
            if len(parts)==2 and parts[1] in ('simple','cascade'):
                self.db.set('referral_rule',parts[1])
                self.db.run('INSERT INTO audit(id,actor,action,created) VALUES (?,?,?,?)',(uuid.uuid4().hex,uid,data,time.time()))
            return self.show_referral_settings(uid)
        if action=='buy': return self.buy(uid,parts[1])
        if action=='proof':
            g=self.group(parts[1])
            if not g or g['role']!='proof' or not g['enabled'] or self.user(uid)['banned']: return
            if not self.proof_eligible(uid,g):
                return self.screen(uid,self.tr(uid,'Invite au moins une personne et attends la validation de son invitation pour commencer ta visite.'),[[button(self.tr(uid,'🔗 Inviter mes amis'),'invite')],[button(self.tr(uid,'⬅️ Accueil'),'home')]])
            if not self.db.one('SELECT 1 FROM proof_used WHERE uid=? AND gid=?',(uid,g['id'])):
                # Time starts on actual admission, not on pressing this button.
                return self.screen(uid,self.tr(uid,'<b>👀 Ta visite découverte</b>')+'\n'+str(self.proof_minutes(g))+self.tr(uid,' minutes à partir de ton entrée.'),
                    [[button(self.tr(uid,'🚪 Entrer'),url=self.link(uid,g))],[button(self.tr(uid,'⬅️ Retour'),'home')]])
            return self.show(uid,'g:'+g['id'])
        if action=='setting' and uid in self.owners:
            key=parts[1]
            if key=='words': return self.words_screen(uid)
            if key=='referral_rule': return self.show_referral_settings(uid)
            if key not in ('unban_goal','words','fastpass_description'): return
            self.db.run('UPDATE users SET pending=? WHERE id=?',(data,uid))
            prompt={'referral_rule':'Écris simple ou cascade.','unban_goal':'Envoie le nombre requis (1 à 10000).',
                    'words':'Ajoute un terme interdit.',
                    'fastpass_description':'Envoie le texte commun à tous les accès coupe-file (maximum 1800 caractères).'}[key]
            rows=[[button('↩️ Texte par défaut','resetfastpass')]] if key=='fastpass_description' else []
            return self.screen(uid,prompt,rows+[[button('⬅️ Annuler','admin')]])
        if action in ('roles','role','slots','slot','toggle','edit','ads','adnew','adoff','publish','portalphoto'):
            g=self.group(parts[1])
            if not g or not self.admin(uid,g,fresh=True): return
            feature={'slots':'slot','slot':'slot','ads':'ads','adnew':'ads','adoff':'ads'}.get(action)
            if action in ('edit','toggle') and len(parts)>2 and parts[2] not in ('chat','enabled'):
                feature=parts[2]
            if feature and not allows(g['role'],feature): return self.show(uid,'a:'+g['id'])
            if action=='portalphoto':
                if g['role']!='portal': return self.show(uid,'a:'+g['id'])
                self.db.run('UPDATE users SET pending=? WHERE id=?',(data,uid))
                return self.screen(uid,'🖼 Envoie la photo à afficher avec le texte du portail.',[[button('⬅️ Annuler','a:'+g['id'])]])
            if action=='publish':
                if g['role']=='portal' and g['enabled']: self.publish_portal(g,force=True)
                return self.show(uid,'a:'+g['id'])
            if action=='roles':
                return self.screen(uid,'<b>Type de groupe</b>',[[button(ROLE_LABELS[r],'role:'+g['id']+':'+r)] for r in ROLES])
            if action=='role':
                if parts[2] not in ROLES or uid not in self.owners: return
                if parts[2]=='portal' and self.db.one("SELECT 1 FROM groups WHERE role='portal' AND id!=?",(g['id'],)):
                    return self.screen(uid,'Un seul portail est autorisé. Remplace le portail existant depuis sa fiche.',[[button('⬅️ Retour','a:'+g['id'])]])
                if parts[2]!=g['role']:
                    # Changing role requires explicit activation with its new configuration.
                    if g['enabled']:
                        self.permissions(g,False)
                        if g['role']=='portal': self.remove_notice(g,'portal')
                    self.db.run('UPDATE groups SET role=?,enabled=0,opened=0 WHERE id=?',(parts[2],g['id']))
                    self.db.set('locked:'+g['id'],'')
            if action=='slots':
                return self.screen(uid,'<b>Créneau quotidien</b>',[[button(s,'slot:'+g['id']+':'+s)] for s in SLOTS])
            if action=='slot' and parts[2] in SLOTS:
                self.db.run('UPDATE groups SET slot=? WHERE id=?',(parts[2],g['id']))
            if action=='toggle':
                field=parts[2]
                if field not in ('enabled','locker','monthly','anti'): return
                if field=='enabled':
                    if uid not in self.owners: return
                    if not g['enabled']:
                        if g['role'] in ('chat','always','vip') and not g['description'].strip():
                            return self.screen(uid,'📝 Ajoute une description avant d’activer ce groupe : elle sera visible par les membres.',[[button('📝 Description','edit:'+g['id']+':description'),button('⬅️ Retour','a:'+g['id'])]])
                        if g['role']=='unset' or (g['role']=='vip' and (not g['description'].strip() or not self.payment_options(g['id']))):
                            return self.screen(uid,'Complète le rôle, et pour un VIP la description et le prix.',[[button('⬅️ Retour','a:'+g['id'])]])
                        try:
                            self.check_rights(g)
                            self.clear_group_pause(g)
                        except (ValueError,APIError) as error:
                            return self.screen(uid,'⚠️ Activation impossible : '+html.escape(str(error)),[[button('⬅️ Retour','a:'+g['id'])]])
                    else:
                        if g['role']=='portal': self.remove_notice(g,'portal')
                        else: self.permissions(g,False)
                        self.db.run('UPDATE groups SET opened=0 WHERE id=?',(g['id'],))
                        self.db.set('locked:'+g['id'],'')
                self.db.run(f'UPDATE groups SET {field}=? WHERE id=?',(1-g[field],g['id']))
                if field=='enabled' and not g['enabled'] and g['role']!='portal':
                    self.db.run('INSERT INTO group_launches(gid,created) VALUES (?,?) ON CONFLICT DO NOTHING',(g['id'],time.time()))
                if field=='enabled' and not g['enabled'] and g['role']=='portal':
                    self.publish_portal(self.group(g['id']),force=True)
            if action=='edit':
                field=parts[2]
                if field not in ('title','description','goal','price','threshold','rules','weekday','chat'): return
                if field=='chat' and uid not in self.owners: return
                self.db.run('UPDATE users SET pending=? WHERE id=?',(data,uid))
                if field=='weekday':
                    from app.utils.group_roles import WEEKDAYS
                    return self.screen(uid,'Choisis le jour d’ouverture.',[[button(day,'daypick:'+g['id']+':'+str(i))] for i,day in enumerate(WEEKDAYS)])
                prompts={'title':'Quel nom veux-tu afficher ?', 'description':'Envoie la présentation du groupe.', 'goal':'Combien d’invitations faut-il pour débloquer ce groupe ?', 'threshold':'Combien de membres présents faut-il pour ouvrir ?', 'rules':'Envoie les règles du groupe.', 'chat':'Ajoute le bot administrateur au nouveau groupe puis envoie son identifiant Telegram négatif.', 'price':'Envoie le nouveau tarif.'}
                return self.screen(uid,prompts[field],[[button('⬅️ Annuler','a:'+g['id'])]])
            if action=='ads':
                ads=self.db.all('SELECT * FROM ads WHERE gid=?',(g['id'],))
                rows=[[button(('🟢 ' if a['enabled'] else '⚪ ')+a['text'][:30],'adoff:'+g['id']+':'+a['id'])] for a in ads]
                return self.screen(uid,'<b>📣 Publicités</b>\nClique une publicité pour activer/désactiver.',rows+[[button('➕ Ajouter','adnew:'+g['id'])],[button('⬅️ Retour','a:'+g['id'])]])
            if action=='adnew':
                self.db.run('UPDATE users SET pending=? WHERE id=?',(data,uid))
                return self.screen(uid,'Envoie un texte ou une photo légendée.\nPremière ligne : 2, 4, 6 ou 12 (intervalle en heures).\nLignes suivantes : ta publicité.',[[button('⬅️ Annuler','a:'+g['id'])]])
            if action=='adoff':
                self.db.run('UPDATE ads SET enabled=1-enabled WHERE id=? AND gid=?',(parts[2],g['id']))
            self.db.run('INSERT INTO audit(id,actor,action,created) VALUES (?,?,?,?)',(uuid.uuid4().hex,uid,data,time.time()))
            return self.show(uid,'a:'+g['id'])
        self.db.run('UPDATE users SET pending=NULL WHERE id=?',(uid,))
        return self.show(uid,data)

    def input(self,uid,m,pending):
        if self.launch_gate(uid): return
        if self.advertising_input(uid,m,pending): return
        if self.workflow_input(uid,m,pending): return
        if self.welcome_input(uid,m,pending): return
        if self.payment_input(uid,m,pending): return
        parts=pending.split(':'); action=parts[0]
        value=m.get('text',m.get('caption','')).strip()
        if action=='setting':
            if uid not in self.owners: return
            key=parts[1]
            if key=='referral_rule' and value not in ('simple','cascade'): raise ValueError('Valeur : simple ou cascade.')
            if key=='fastpass_description' and (not value or len(value)>1800): raise ValueError('Texte requis, maximum 1800 caractères.')
            if key=='unban_goal' and not (value.isdigit() and 1<=int(value)<=10000): raise ValueError('Nombre entre 1 et 10000.')
            if key=='words': return self.words_screen(uid)
            self.db.set(key,value)
            page='admin'
        else:
            g=self.group(parts[1])
            if not g or not self.admin(uid,g,fresh=True): return
            if action=='portalphoto':
                if g['role']!='portal': return self.show(uid,'a:'+g['id'])
                if not m.get('photo'): raise ValueError('Envoie une photo Telegram pour le portail.')
                if len(g['description'].encode('utf-16-le'))//2>800: raise ValueError('Raccourcis d’abord le texte du portail à 800 caractères maximum pour ajouter une image.')
                self.db.set('portal_photo:'+g['id'],m['photo'][-1]['file_id'])
            elif action=='adnew':
                if not allows(g['role'],'ads'): return self.show(uid,'a:'+g['id'])
                hours,sep,body=value.partition('\n')
                if hours not in ('2','4','6','12') or not body: raise ValueError('Première ligne : 2, 4, 6 ou 12. Puis le texte.')
                photo=m['photo'][-1]['file_id'] if m.get('photo') else None
                if len(body)>1000: raise ValueError('Publicité limitée à 1000 caractères.')
                self.db.run('INSERT INTO ads(id,gid,text,photo,interval_hours) VALUES (?,?,?,?,?)',
                            (uuid.uuid4().hex,g['id'],body,photo,int(hours)))
            else:
                field=parts[2]
                if field!='chat' and not allows(g['role'],field): return self.show(uid,'a:'+g['id'])
                if field in ('goal','price','threshold','weekday'):
                    if not value.isdigit(): raise ValueError('Envoie un nombre entier positif.')
                    value=int(value)
                    low,high=(0,6) if field=='weekday' else ((0,10000) if field=='price' else (1,10000))
                    if not low<=value<=high: raise ValueError(f'Nombre entre {low} et {high}.')
                elif field=='chat':
                    if uid not in self.owners: return
                    self.replace_group(uid,g,int(value))
                    value=str(value)
                elif field=='title':
                    if not value or len(value)>100: raise ValueError('Nom affiché requis, maximum 100 caractères.')
                elif field not in ('description','rules'): return
                elif len(value)>2500: raise ValueError('Maximum 2500 caractères.')
                if field=='description':
                    if g['role'] in ('chat','always','vip') and not value: raise ValueError('La description ne peut pas être vide.')
                    if g['role']=='portal' and self.db.setting('portal_photo:'+g['id']) and len(value.encode('utf-16-le'))//2>800:
                        raise ValueError('Texte avec image : maximum 800 caractères.')
                if field!='chat': self.db.run(f'UPDATE groups SET {field}=? WHERE id=?',(value,g['id']))
            current=self.group(g['id'])
            if current['role']=='portal' and current['enabled']:self.publish_portal(current,force=True)
            page='a:'+g['id']
        self.db.run('UPDATE users SET pending=NULL WHERE id=?',(uid,))
        self.show(uid,page)

