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
        if user['permanent']:
            return self.screen(uid, 'Accès indisponible.', [])
        if page == 'invite':
            portal = self.db.one("SELECT * FROM groups WHERE role='portal' AND enabled=1")
            if not portal: return self.screen(uid, 'Le portail sera bientôt disponible.', [[button('⬅️ Accueil','home')]])
            url = self.link(uid, portal, 'referral')
            share = 'https://t.me/share/url?' + urlencode({'url':url,'text':'Rejoins notre portail !'})
            return self.screen(uid, '<b>🔗 Invite tes amis</b>\n\nTon lien personnel mène au portail. Tes invitations font progresser les groupes classiques et 24 h/24 en parallèle, chacun avec son objectif.\n\n' + html.escape(url),
                               [[button('📤 Partager mon invitation',url=share)], [button('⬅️ Accueil','home')]])
        if page == 'unban':
            if user['banned']:
                target = int(self.db.setting('unban_goal','10'))
                n = max(0, self.count(uid)-user['ban_base'])
                if n >= target:
                    self.db.run('UPDATE users SET banned=0 WHERE id=?',(uid,))
                    for g in self.db.all("SELECT * FROM groups WHERE enabled=1 AND role!='portal'"):
                        self.enqueue('unbanChatMember',dict(chat_id=g['chat'],user_id=uid,only_if_banned=True,_unless_banned=True))
                    return self.show(uid)
                return self.screen(uid, '<b>Demande de débannissement</b>\n\n'+progress(n,target),
                    [[button('🔗 Inviter','invite')],[button('🔄 Actualiser','unban'),button('⬅️ Accueil','home')]])
            return self.show(uid)
        if page.startswith('g:'):
            g = self.group(page[2:])
            if not g or not g['enabled']: return self.show(uid)
            if user['banned']: return self.show(uid,'unban')
            self.metric('view',uid,g['id'],key=f'view:{uid}:{g["id"]}:{int(time.time()//3600)}')
            a = self.access(uid,g['id'])
            n = max(0,self.count(uid)-(a['base'] if a else 0))
            if g['role'] in ('chat','always') and not self.entitled(uid,g) and n >= g['goal']:
                self.grant(uid,g,'referral')
            description=g['description'].strip() or 'La présentation de ce groupe est en cours de préparation.'
            state='🟢 Ouvert' if g['opened'] else '🔴 Fermé'
            text = f'<b>{html.escape(g["title"])}</b>\n{ROLE_LABELS.get(g["role"],g["role"])} · {state}\n\n{html.escape(description)}\n\n'
            rows = []
            if self.entitled(uid,g):
                text += '✅ <b>Ton accès est débloqué.</b>'
                rows += [[button('🚪 Rejoindre le groupe',url=self.link(uid,g))]]
            elif g['role'] == 'proof':
                used = self.db.one('SELECT 1 FROM proof_used WHERE uid=? AND gid=?',(uid,g['id']))
                text += 'Visite déjà utilisée.' if used else 'Une visite de 10 minutes, une seule fois.'
                if not used: rows += [[button('👀 Commencer ma visite', 'proof:'+g['id'])]]
            else:
                if g['role'] != 'vip':
                    text += '<b>🎯 TON ACCÈS</b>\n<code>'+progress(n,g['goal']).split('\n')[0]+'</code>\n<b>'+str(n)+' / '+str(g['goal'])+' invitations validées</b>\nEncore '+str(max(0,g['goal']-n))+' pour débloquer ce groupe.\n\n'
                    rows += [[button('🔗 Inviter mes amis','invite')]]
                pending = self.db.one("SELECT id FROM manual_orders WHERE uid=? AND gid=? AND status IN ('awaiting_proof','review')",(uid,g['id']))
                if self.payment_options(g['id']) or pending:
                    label = '⏳ Reprendre mon paiement' if pending else ('⚡ Accès coupe-file' if g['role']!='vip' else '💳 Obtenir cet accès')
                    if g['monthly'] and not pending: label += ' / 30 jours'
                    rows += [[button(label,'buy:'+g['id'])]]
            rows += [[button('🔄 Actualiser',page),button('⬅️ Accueil','home')]]
            return self.screen(uid,text,rows)
        if page == 'admin' and uid in self.owners:
            rows = [[button(('✅ ' if g['enabled'] else '⚙️ ')+g['title'],'a:'+g['id'])]
                    for g in self.db.all('SELECT * FROM groups ORDER BY title')]
            rows += [[button('📊 Statistiques','stats:7'),button('🩺 État du réseau','health')],
                     [button('⚙️ Configuration','configuration')],
                     [button('🔗 Règle invitations','setting:referral_rule'),button('🔓 Débannissement','setting:unban_goal')],
                     [button('💳 Paiements','payments'),button('⚙️ Moyens de paiement','payconfig')],
                     [button('🚫 Mots interdits','setting:words')],[button('⬅️ Accueil','home')]]
            return self.screen(uid,'<b>🛠 Administration du réseau</b>\nAjoute le bot administrateur à un supergroupe pour le connecter.',rows)
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
        text='<b>✨ Ton espace</b>\n\nChoisis ton prochain groupe.'
        opened=[g for g in groups if g['opened'] and g['role']=='chat']
        if opened: text+='\n\n🔥 '+html.escape(opened[0]['title'])+' est ouvert !'
        if not groups: text+='\nLes groupes arrivent bientôt.'
        rows += [[button('🔗 Inviter mes amis','invite')]]
        if user['banned']: rows += [[button('🔓 Demande de débannissement','unban')]]
        if uid in self.owners: rows += [[button('🛠 Administration','admin')]]
        else:
            for g in groups:
                try:
                    if self.admin(uid,g): rows += [[button('⚙️ Gérer '+g['title'],'a:'+g['id'])]]
                except Exception as error:
                    self.record_failure('admin_lookup:'+g['id'],error)
        text+='\n\n<i>Accueil actualisé le '+self.time_label(time.time())+'</i>'
        rows += [[button('🔄 Actualiser','home')]]
        return self.screen(uid,text,rows)

