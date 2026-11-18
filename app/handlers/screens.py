import html
import time
from urllib.parse import urlencode
from app.utils.core import progress
from app.keyboards.inline import button
from app.keyboards.admin_groups import group_controls
from app.utils.group_roles import ROLE_LABELS

class Screens:
    def show(self, uid, page='home'):
        user = self.user(uid)
        if not user['permanent'] and self.launch_gate(uid): return
        if page=='language':
            from app.utils.i18n import LANGUAGES
            return self.screen(uid,self.tr(uid,'Choisis ta langue.'),
                [[button(('✅ ' if code==self.language(uid) else '')+name,'language:'+code)] for code,name in LANGUAGES.items()]+
                [[button(self.tr(uid,'⬅️ Accueil'),'member')]])
        if user['permanent']:
            return self.screen(uid, self.tr(uid,'Accès indisponible.'), [])
        if page=='home':
            if uid in self.owners:
                page='admin'
            else:
                managed=[]
                for candidate in self.db.all('SELECT * FROM groups WHERE enabled=1 ORDER BY title'):
                    try:
                        if self.admin(uid,candidate): managed.append(candidate)
                    except Exception as error: self.record_failure('admin_lookup:'+candidate['id'],error)
                if managed:
                    rows=[[button('⚙️ '+g['title'],'a:'+g['id'])] for g in managed]
                    rows += [[button(self.tr(uid,'🔗 Inviter mes amis'),'invite'),button('👤 Vue membre','member')],[button(self.tr(uid,'🔄 Actualiser'),'home')]]
                    return self.screen(uid,'<b>🛠 Administration de tes groupes</b>',rows)
        if page == 'invite' or page.startswith('invite:'): return self.invitation_screen(uid,page.partition(':')[2] or None)
        if page == 'unban':
            if user['banned']:
                target = int(self.db.setting('unban_goal','10'))
                n = max(0, self.count(uid)-user['ban_base'])
                if n >= target:
                    self.db.run('UPDATE users SET banned=0 WHERE id=?',(uid,))
                    self.db.set('link_ban:'+str(uid),'')
                    for g in self.db.all("SELECT * FROM groups WHERE enabled=1"):
                        self.enqueue('unbanChatMember',dict(chat_id=g['chat'],user_id=uid,only_if_banned=True,_unless_banned=True))
                    return self.show(uid)
                return self.screen(uid, self.tr(uid,'<b>Demande de débannissement</b>\n\n')+progress(n,target),
                    [[button(self.tr(uid,'🔗 Inviter'),'invite')],[button(self.tr(uid,'🔄 Actualiser'),'unban'),button(self.tr(uid,'⬅️ Accueil'),'home')]])
            return self.show(uid)
        if page.startswith('g:'):
            g = self.group(page[2:])
            if not g or not g['enabled']: return self.show(uid)
            if user['banned']: return self.show(uid,'unban')
            self.metric('view',uid,g['id'],key=f'view:{uid}:{g["id"]}:{int(time.time()//3600)}')
            a = self.access(uid,g['id'])
            n = max(0,self.count(uid,g['id'])-(a['base'] if a else 0))
            if g['role'] in ('chat','always') and not self.entitled(uid,g) and n >= g['goal']:
                self.grant(uid,g,'referral')
            description=self.db.setting('description:'+g['id']+':'+self.language(uid),g['description']).strip() or self.tr(uid,'La présentation de ce groupe est en cours de préparation.')
            state=self.tr(uid,'🟢 Ouvert') if g['opened'] else self.tr(uid,'🔴 Fermé')
            text = f'<b>{html.escape(g["title"])}</b>\n{self.tr(uid,ROLE_LABELS.get(g["role"],g["role"]))} · {state}\n\n{html.escape(description)}\n\n'
            rows = []
            if self.entitled(uid,g):
                text += self.tr(uid,'✅ <b>Ton accès est débloqué.</b>')
                rows += [[button(self.tr(uid,'🚪 Rejoindre le groupe'),url=self.link(uid,g))]]
            elif g['role'] == 'proof':
                used = self.db.one('SELECT 1 FROM proof_used WHERE uid=? AND gid=?',(uid,g['id']))
                text += self.tr(uid,'Visite déjà utilisée.') if used else self.tr(uid,'Une visite de 10 minutes, une seule fois.')
                if not used: rows += [[button(self.tr(uid,'👀 Commencer ma visite'), 'proof:'+g['id'])]]
            else:
                if g['role'] != 'vip':
                    text += self.tr(uid,'<b>🎯 TON ACCÈS</b>\n<code>')+progress(n,g['goal']).split('\n')[0]+'</code>\n<b>'+str(n)+' / '+str(g['goal'])+self.tr(uid,' invitations validées</b>\nEncore ')+str(max(0,g['goal']-n))+self.tr(uid,' pour débloquer ce groupe.\n\n')
                    rows += [[button(self.tr(uid,'🔗 Inviter mes amis'),'invite:'+g['id'])]]
                pending = self.db.one("SELECT id FROM manual_orders WHERE uid=? AND gid=? AND status IN ('awaiting_proof','review')",(uid,g['id']))
                if self.payment_options(g['id']) or pending:
                    label = self.tr(uid,'⏳ Reprendre mon paiement') if pending else (self.tr(uid,'⚡ Accès coupe-file') if g['role']!='vip' else self.tr(uid,'💳 Obtenir cet accès'))
                    if g['monthly'] and not pending: label += self.tr(uid,' / 30 jours')
                    rows += [[button(label,'buy:'+g['id'])]]
            rows += [[button(self.tr(uid,'🔄 Actualiser'),page),button(self.tr(uid,'⬅️ Accueil'),'home')]]
            return self.screen(uid,text,rows)
        if page == 'admin' and uid in self.owners:
            rows = [[button(('✅ ' if g['enabled'] else '⚙️ ')+g['title'],'a:'+g['id'])]
                    for g in self.db.all('SELECT * FROM groups ORDER BY title')]
            rows += [[button('📊 Statistiques','stats:7'),button('🩺 État du réseau','health')],
                     [button('⚙️ Configuration','configuration')],
                     [button('🔗 Règle invitations','setting:referral_rule'),button('🔓 Débannissement','setting:unban_goal')],
                     [button('💳 Paiements','payments'),button('⚙️ Moyens de paiement','payconfig')],
                     [button('📝 Texte coupe-file','setting:fastpass_description')],
                     [button('🖼 Accueil et traductions','welcomeconfig')],
                     [button('🚫 Mots interdits','words')],
                     [button('🐞 Signalements','bugs')],
                     [button(self.tr(uid,'🔗 Inviter mes amis'),'invite'),button('👤 Vue membre','member')],
                     [button(self.tr(uid,'🔄 Actualiser'),'admin')]]
            return self.screen(uid,'<b>🛠 Administration du réseau</b>\nAjoute le bot administrateur à un supergroupe pour le connecter.\n\n<i>Actualisé le '+self.time_label(time.time())+'</i>',rows)
        if page.startswith('a:'):
            g=self.group(page[2:])
            if not g or not self.admin(uid,g): return self.show(uid)
            rows=group_controls(g,uid in self.owners)
            text=f'<b>⚙️ {html.escape(g["title"])}</b>\nType : {ROLE_LABELS.get(g["role"],g["role"])}\nIdentifiant : {g["chat"]}'
            text+='\nÉtat : '+('🟢 Activé' if g['enabled'] else '⚪ Désactivé')
            if g['role']=='portal':
                published=self.db.one("SELECT mid FROM notices WHERE gid=? AND kind='portal'",(g['id'],))
                text+='\nMessage : '+('publié' if published else 'pas encore publié')
                if not g['enabled']: text+='\nAppuie sur « Activer et publier » pour afficher le portail dans le groupe.'
                text+='\nLe portail fonctionne même sans groupe de chat.'
                error=self.db.setting('portal_error:'+g['id'])
                if error: text+='\n⚠️ '+html.escape(error)
            elif g['role']=='always': text+='\nOuvert 24 h/24, sous réserve du locker.'
            elif g['role']=='proof': text+='\nVisite de 10 minutes, une seule fois par personne.'
            elif g['role']=='unset': text+='\nChoisis le rôle pour afficher les réglages correspondants.'
            if g['role'] in ('chat','always','vip') and not self.payment_options(g['id']):
                text+='\n⚠️ Paiement indisponible : configure au moins un tarif et ses coordonnées de réception.'
            text+='\nModifications sauvegardées immédiatement.'
            return self.screen(uid,text,rows)
        groups=self.db.all("SELECT * FROM groups WHERE enabled=1 AND role!='portal' ORDER BY title")
        rows=[[button(g['title']+(' · ✅' if self.entitled(uid,g) else ''),'g:'+g['id'])] for g in groups]
        text=self.tr(uid,'<b>✨ Ton espace</b>\n\nChoisis ton prochain groupe.')
        custom=self.db.setting('welcome_text:'+self.language(uid))
        if custom: text=html.escape(custom)
        opened=[g for g in groups if g['opened'] and g['role']=='chat']
        if opened: text+='\n\n🔥 '+html.escape(opened[0]['title'])+self.tr(uid,' est ouvert !')
        if not groups: text+=self.tr(uid,'\nLes groupes arrivent bientôt.')
        rows += [[button(self.tr(uid,'🔗 Inviter mes amis'),'invite')]]
        if user['banned']: rows += [[button(self.tr(uid,'🔓 Demande de débannissement'),'unban')]]
        if uid in self.owners: rows += [[button('🛠 Administration','admin')]]
        else:
            for g in groups:
                try:
                    if self.admin(uid,g): rows += [[button('⚙️ Gérer '+g['title'],'a:'+g['id'])]]
                except Exception as error:
                    self.record_failure('admin_lookup:'+g['id'],error)
        text+=self.tr(uid,'\n\n<i>Accueil actualisé le ')+self.time_label(time.time())+'</i>'
        rows += [[button(self.tr(uid,'🐞 Signaler un bug · +5'),'bug')]]
        rows += [[button(self.tr(uid,'🌐 Langue'),'language'),button(self.tr(uid,'🔄 Actualiser'),'member')]]
        return self.screen(uid,text,rows,photo=self.db.setting('welcome_photo') or None)

