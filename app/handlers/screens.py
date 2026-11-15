import html
from urllib.parse import urlencode
from app.utils.core import progress
from app.keyboards.inline import button

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
            return self.screen(uid, '<b>🔗 Invite tes amis</b>\n\nTon lien personnel mène au portail.\n' + html.escape(url),
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
            a = self.access(uid,g['id'])
            n = max(0,self.count(uid)-(a['base'] if a else 0))
            if g['role'] in ('chat','always') and not self.entitled(uid,g) and n >= g['goal']:
                self.grant(uid,g,'referral')
            text = f'<b>{html.escape(g["title"])}</b>\n\n{html.escape(g["description"])}\n\n'
            rows = []
            if self.entitled(uid,g):
                text += '✅ Ton accès est disponible.'
                rows += [[button('🚪 Rejoindre le groupe',url=self.link(uid,g))]]
            elif g['role'] == 'proof':
                used = self.db.one('SELECT 1 FROM proof_used WHERE uid=? AND gid=?',(uid,g['id']))
                text += 'Visite déjà utilisée.' if used else 'Une visite de 10 minutes, une seule fois.'
                if not used: rows += [[button('👀 Commencer ma visite', 'proof:'+g['id'])]]
            else:
                if g['role'] != 'vip':
                    text += progress(n,g['goal'])+' invitations validées\n'
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
            rows += [[button('🔗 Règle invitations','setting:referral_rule'),button('🔓 Débannissement','setting:unban_goal')],
                     [button('💳 Paiements','payments'),button('⚙️ Moyens de paiement','payconfig')],
                     [button('🚫 Mots interdits','setting:words')],[button('⬅️ Accueil','home')]]
            return self.screen(uid,'<b>🛠 Administration du réseau</b>\nAjoute le bot administrateur à un supergroupe pour le connecter.',rows)
        if page.startswith('a:'):
            g=self.group(page[2:])
            if not g or not self.admin(uid,g): return self.show(uid)
            rows = [[button('Rôle : '+g['role'],'roles:'+g['id'])],
                    [button('📝 Description','edit:'+g['id']+':description'),button('🎯 Objectif','edit:'+g['id']+':goal')],
                    [button('💳 Tarifs','prices:'+g['id']),button('30 jours : '+str(bool(g['monthly'])),'toggle:'+g['id']+':monthly')],
                    [button('🔒 Locker : '+('ON' if g['locker'] else 'OFF'),'toggle:'+g['id']+':locker'),button('Seuil : '+str(g['threshold']),'edit:'+g['id']+':threshold')],
                    [button('🕐 '+g['slot'],'slots:'+g['id']),button('Jour gratuit : '+str(g['weekday']),'edit:'+g['id']+':weekday')],
                    [button('📜 Règles','edit:'+g['id']+':rules'),button('Anti-repost : '+str(bool(g['anti'])),'toggle:'+g['id']+':anti')],
                    [button('📣 Publicités','ads:'+g['id'])]]
            if uid in self.owners:
                rows += [[button('✅ Activer' if not g['enabled'] else '⏸ Désactiver','toggle:'+g['id']+':enabled')],
                         [button('🔁 Remplacer le groupe Telegram','edit:'+g['id']+':chat')]]
            rows += [[button('⬅️ Administration','admin' if uid in self.owners else 'home')]]
            return self.screen(uid,f'<b>⚙️ {html.escape(g["title"])}</b>\nIdentifiant : {g["chat"]}\nModifications sauvegardées immédiatement.',rows)
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
                if self.admin(uid,g): rows += [[button('⚙️ Gérer '+g['title'],'a:'+g['id'])]]
        rows += [[button('🔄 Actualiser','home')]]
        return self.screen(uid,text,rows)

