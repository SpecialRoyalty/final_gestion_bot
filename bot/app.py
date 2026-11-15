import html
import json
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from .core import ROLES, SLOTS, progress, active_access, member_present, window, forbidden, countdown
from .telegram import APIError


def button(text, data=None, url=None):
    return {'text': text, **({'url': url} if url else {'callback_data': data})}


def keyboard(*rows):
    return {'inline_keyboard': list(rows)}


class App:
    def __init__(self, db, api, owners, tz='Europe/Paris', support='Administrateur'):
        self.db, self.api, self.owners, self.tz, self.support = db, api, set(owners), tz, support
        self.me = api.call('getMe')
        self.last_tick = 0

    def user(self, uid):
        self.db.run('INSERT INTO users(id) VALUES (?) ON CONFLICT(id) DO NOTHING', (uid,))
        return self.db.one('SELECT * FROM users WHERE id=?', (uid,))

    def group(self, gid):
        return self.db.one('SELECT * FROM groups WHERE id=?', (gid,))

    def deep(self, page='home'):
        return f'https://t.me/{self.me["username"]}?start={page}'

    def admin(self, uid, group=None):
        if uid in self.owners: return True
        if not group or not group['enabled']: return False
        member = self.api.call('getChatMember', chat_id=group['chat'], user_id=uid)
        return member['status'] in ('administrator', 'creator')

    def alert(self, text):
        # Hourly deduplication, failed alerts are queued just like other operations.
        key = 'alert:' + text[:160]
        if float(self.db.setting(key, '0')) > time.time()-3600: return
        self.db.set(key, time.time())
        for uid in self.owners:
            self.enqueue('sendMessage', {'chat_id': uid, 'text': '⚠️ ' + text})

    def enqueue(self, method, payload, due=None):
        self.db.run('INSERT INTO jobs(id,method,payload,due) VALUES (?,?,?,?)',
                    (uuid.uuid4().hex, method, json.dumps(payload), due or time.time()))

    def screen(self, uid, text, rows):
        user = self.user(uid)
        data = dict(chat_id=uid, text=text, parse_mode='HTML', reply_markup=keyboard(*rows),
                    link_preview_options={'is_disabled': True})
        if user['menu']:
            try:
                self.api.call('editMessageText', message_id=user['menu'], **data)
                return
            except APIError as e:
                if 'message is not modified' in str(e): return
                if not any(t in str(e) for t in ('message to edit not found', "message can't be edited")):
                    raise
        result = self.api.call('sendMessage', **data)
        self.db.run('UPDATE users SET menu=? WHERE id=?', (result['message_id'], uid))

    def count(self, uid):
        return self.db.one('SELECT COUNT(*) AS n FROM referrals WHERE inviter=? AND valid=1', (uid,))['n']

    def access(self, uid, gid):
        return self.db.one('SELECT * FROM access WHERE uid=? AND gid=?', (uid, gid))

    def entitled(self, uid, g):
        u = self.user(uid)
        if u['permanent'] or (u['banned'] and g['role'] != 'portal'): return False
        if g['role'] in ('portal', 'free'): return True
        return active_access(self.access(uid, g['id']), time.time())

    def grant(self, uid, g, source, expires=None):
        self.db.run('INSERT INTO access(uid,gid,source,expires) VALUES (?,?,?,?) '
                    'ON CONFLICT(uid,gid) DO UPDATE SET source=excluded.source,expires=excluded.expires,revoked=0',
                    (uid,g['id'],source,expires))

    def link(self, uid, g, purpose='access'):
        row = self.db.one('SELECT url FROM links WHERE uid=? AND gid=? AND purpose=? AND expires>?',
                          (uid,g['id'],purpose,time.time()+60))
        if purpose != 'referral' and self.entitled(uid,g):
            self.api.call('unbanChatMember',chat_id=g['chat'],user_id=uid,only_if_banned=True)
        if row: return row['url']
        expires = int(time.time()+86400 if purpose == 'referral' else time.time()+600)
        data = dict(chat_id=g['chat'], name=f'{purpose[:3]}:{uid}', expire_date=expires)
        if purpose != 'referral': data['creates_join_request'] = True
        result = self.api.call('createChatInviteLink', **data)
        url = result['invite_link']
        self.db.run('INSERT INTO links(url,gid,uid,purpose,expires) VALUES (?,?,?,?,?)',
                    (url,g['id'],uid,purpose,expires))
        return url

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
                pending = self.db.one("SELECT id FROM orders WHERE uid=? AND gid=? AND status IN ('pending','checkout')",(uid,g['id']))
                if g['price'] > 0:
                    label = '⏳ Reprendre mon paiement' if pending else f'⚡ Accès — {g["price"]} ⭐' + (' / 30 jours' if g['monthly'] else '')
                    rows += [[button(label,'buy:'+g['id'])]]
            rows += [[button('🔄 Actualiser',page),button('⬅️ Accueil','home')]]
            return self.screen(uid,text,rows)
        if page == 'admin' and uid in self.owners:
            rows = [[button(('✅ ' if g['enabled'] else '⚙️ ')+g['title'],'a:'+g['id'])]
                    for g in self.db.all('SELECT * FROM groups ORDER BY title')]
            rows += [[button('🔗 Règle invitations','setting:referral_rule'),button('🔓 Débannissement','setting:unban_goal')],
                     [button('🚫 Mots interdits','setting:words')],[button('⬅️ Accueil','home')]]
            return self.screen(uid,'<b>🛠 Administration du réseau</b>\nAjoute le bot administrateur à un supergroupe pour le connecter.',rows)
        if page.startswith('a:'):
            g=self.group(page[2:])
            if not g or not self.admin(uid,g): return self.show(uid)
            rows = [[button('Rôle : '+g['role'],'roles:'+g['id'])],
                    [button('📝 Description','edit:'+g['id']+':description'),button('🎯 Objectif','edit:'+g['id']+':goal')],
                    [button('⭐ Prix en Stars','edit:'+g['id']+':price'),button('30 jours : '+str(bool(g['monthly'])),'toggle:'+g['id']+':monthly')],
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

    def buy(self, uid, gid):
        g=self.group(gid)
        if not g or not g['enabled'] or g['price']<=0 or self.user(uid)['banned'] or self.entitled(uid,g):
            return self.show(uid,'g:'+gid)
        with self.db.transaction():
            order=self.db.one("SELECT * FROM orders WHERE uid=? AND gid=? AND status IN ('pending','checkout')",(uid,gid))
            if not order:
                oid=uuid.uuid4().hex
                self.db.run("INSERT INTO orders(id,uid,gid,amount,monthly,status,created) VALUES (?,?,?,?,?,'pending',?)",
                            (oid,uid,gid,g['price'],g['monthly'],time.time()))
                order=self.db.one('SELECT * FROM orders WHERE id=?',(oid,))
        if not order['invoice']:
            url=self.api.call('createInvoiceLink',title=g['title'][:32],description=g['description'][:255] or 'Accès au groupe',
                payload=order['id'],provider_token='',currency='XTR',prices=[{'label':'Accès','amount':order['amount']}])
            self.db.run('UPDATE orders SET invoice=? WHERE id=?',(url,order['id']))
            order['invoice']=url
        self.screen(uid,'<b>💳 Ton accès</b>\n\n'+str(order['amount'])+' Stars'+('\nDurée : 30 jours, renouvellement manuel.' if order['monthly'] else '\nPaiement unique.'),
            [[button('⭐ Payer avec Telegram',url=order['invoice'])],[button('🔄 Vérifier mon accès','g:'+gid),button('⬅️ Retour','g:'+gid)]])

    def checkout(self,q):
        o=self.db.one('SELECT * FROM orders WHERE id=?',(q['invoice_payload'],))
        g=self.group(o['gid']) if o else None
        ok=bool(o and g and g['enabled'] and o['uid']==q['from']['id'] and o['status']=='pending'
                and q['currency']=='XTR' and q['total_amount']==o['amount']
                and not self.user(o['uid'])['banned'] and not self.entitled(o['uid'],g))
        if ok: self.db.run("UPDATE orders SET status='checkout' WHERE id=?",(o['id'],))
        self.api.call('answerPreCheckoutQuery',pre_checkout_query_id=q['id'],ok=ok,
                      **({} if ok else {'error_message':'Cet achat est indisponible ou cet accès est déjà acquis.'}))

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

    def callback(self,q):
        uid=q['from']['id']; data=q.get('data','home')
        self.api.call('answerCallbackQuery',callback_query_id=q['id'])
        if q.get('message',{}).get('chat',{}).get('id')!=uid: return
        if self.user(uid)['permanent']: return self.show(uid)
        parts=data.split(':'); action=parts[0]
        if action=='buy': return self.buy(uid,parts[1])
        if action=='proof':
            g=self.group(parts[1])
            if not g or g['role']!='proof' or not g['enabled'] or self.user(uid)['banned']: return
            if not self.db.one('SELECT 1 FROM proof_used WHERE uid=? AND gid=?',(uid,g['id'])):
                # Time starts on actual admission, not on pressing this button.
                return self.screen(uid,'<b>👀 Ta visite découverte</b>\n10 minutes à partir de ton entrée.',
                    [[button('🚪 Entrer',url=self.link(uid,g))],[button('⬅️ Retour','home')]])
            return self.show(uid,'g:'+g['id'])
        if action=='setting' and uid in self.owners:
            key=parts[1]
            if key not in ('referral_rule','unban_goal','words'): return
            self.db.run('UPDATE users SET pending=? WHERE id=?',(data,uid))
            prompt={'referral_rule':'Écris simple ou cascade.','unban_goal':'Envoie le nombre requis (1 à 10000).',
                    'words':'Envoie les termes interdits, un par ligne. Un tiret pour vider.'}[key]
            return self.screen(uid,prompt,[[button('⬅️ Annuler','admin')]])
        if action in ('roles','role','slots','slot','toggle','edit','ads','adnew','adoff'):
            g=self.group(parts[1])
            if not g or not self.admin(uid,g): return
            if action=='roles':
                return self.screen(uid,'<b>Type de groupe</b>',[[button(r,'role:'+g['id']+':'+r)] for r in ROLES])
            if action=='role':
                if parts[2] not in ROLES or uid not in self.owners: return
                if parts[2]=='portal' and self.db.one("SELECT 1 FROM groups WHERE role='portal' AND id!=?",(g['id'],)):
                    return self.screen(uid,'Un seul portail est autorisé. Remplace le portail existant depuis sa fiche.',[[button('⬅️ Retour','a:'+g['id'])]])
                self.db.run('UPDATE groups SET role=? WHERE id=?',(parts[2],g['id']))
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
                        if g['role']=='unset' or (g['role']=='vip' and (not g['description'].strip() or g['price']<=0)):
                            return self.screen(uid,'Complète le rôle, et pour un VIP la description et le prix.',[[button('⬅️ Retour','a:'+g['id'])]])
                        self.check_rights(g)
                    else:
                        self.permissions(g,False)
                        self.db.run('UPDATE groups SET opened=0 WHERE id=?',(g['id'],))
                        self.db.set('locked:'+g['id'],'')
                self.db.run(f'UPDATE groups SET {field}=? WHERE id=?',(1-g[field],g['id']))
            if action=='edit':
                field=parts[2]
                if field not in ('description','goal','price','threshold','rules','weekday','chat'): return
                if field=='chat' and uid not in self.owners: return
                self.db.run('UPDATE users SET pending=? WHERE id=?',(data,uid))
                return self.screen(uid,'Envoie la nouvelle valeur pour <b>'+field+'</b>.\nPour remplacer un groupe : son identifiant Telegram négatif, après avoir ajouté le bot administrateur.',[[button('⬅️ Annuler','a:'+g['id'])]])
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
        parts=pending.split(':'); action=parts[0]
        value=m.get('text',m.get('caption','')).strip()
        if action=='setting':
            if uid not in self.owners: return
            key=parts[1]
            if key=='referral_rule' and value not in ('simple','cascade'): raise ValueError('Valeur : simple ou cascade.')
            if key=='unban_goal' and not (value.isdigit() and 1<=int(value)<=10000): raise ValueError('Nombre entre 1 et 10000.')
            self.db.set(key,'' if value=='-' else value)
            page='admin'
        else:
            g=self.group(parts[1])
            if not g or not self.admin(uid,g): return
            if action=='adnew':
                hours,sep,body=value.partition('\n')
                if hours not in ('2','4','6','12') or not body: raise ValueError('Première ligne : 2, 4, 6 ou 12. Puis le texte.')
                photo=m['photo'][-1]['file_id'] if m.get('photo') else None
                if len(body)>1000: raise ValueError('Publicité limitée à 1000 caractères.')
                self.db.run('INSERT INTO ads(id,gid,text,photo,interval_hours) VALUES (?,?,?,?,?)',
                            (uuid.uuid4().hex,g['id'],body,photo,int(hours)))
            else:
                field=parts[2]
                if field in ('goal','price','threshold','weekday'):
                    if not value.isdigit(): raise ValueError('Envoie un nombre entier positif.')
                    value=int(value)
                    low,high=(0,6) if field=='weekday' else ((0,10000) if field=='price' else (1,10000))
                    if not low<=value<=high: raise ValueError(f'Nombre entre {low} et {high}.')
                elif field=='chat':
                    if uid not in self.owners: return
                    value=int(value)
                    candidate={**g,'chat':value}
                    self.check_rights(candidate)
                    registered=self.db.one('SELECT * FROM groups WHERE chat=?',(value,))
                    if registered and (registered['enabled'] or registered['role']!='unset'):
                        raise ValueError('Ce groupe appartient déjà à une configuration. Utilise un nouveau groupe.')
                    with self.db.transaction():
                        if registered: self.db.run('DELETE FROM groups WHERE id=?',(registered['id'],))
                        self.db.run('UPDATE groups SET chat=?,opened=0 WHERE id=?',(value,g['id']))
                        self.db.run('DELETE FROM notices WHERE gid=?',(g['id'],))
                        self.db.run('DELETE FROM links WHERE gid=?',(g['id'],))
                        self.db.run('UPDATE members SET present=0 WHERE gid=?',(g['id'],))
                    for a in self.db.all('SELECT * FROM access WHERE gid=? AND revoked=0',(g['id'],)):
                        if active_access(a,time.time()):
                            self.enqueue('sendMessage',dict(chat_id=a['uid'],text='Ton groupe a été remplacé. Ton accès est conservé.',reply_markup=keyboard([button('🚪 Retrouver mon accès',url=self.deep('g_'+g['id']))])))
                    value=str(value)
                elif field not in ('description','rules'): return
                elif len(value)>2500: raise ValueError('Maximum 2500 caractères.')
                if field!='chat': self.db.run(f'UPDATE groups SET {field}=? WHERE id=?',(value,g['id']))
            page='a:'+g['id']
        self.db.run('UPDATE users SET pending=NULL WHERE id=?',(uid,))
        self.show(uid,page)

    def check_rights(self,g):
        chat=self.api.call('getChat',chat_id=g['chat'])
        if chat['type']!='supergroup': raise ValueError('Utilise un supergroupe Telegram privé.')
        if chat.get('username'): raise ValueError('Le groupe doit être privé pour contrôler les accès.')
        member=self.api.call('getChatMember',chat_id=g['chat'],user_id=self.me['id'])
        required=('can_delete_messages','can_restrict_members','can_invite_users')
        if member['status']!='administrator' or not all(member.get(r) for r in required):
            raise ValueError('Le bot doit être administrateur : supprimer, restreindre, inviter.')

    def ban(self,uid,permanent=False):
        self.user(uid)
        self.db.run('UPDATE users SET banned=1,permanent=CASE WHEN permanent=1 THEN 1 ELSE ? END,ban_base=? WHERE id=?',
                    (int(permanent),self.count(uid),uid))
        for g in self.db.all('SELECT * FROM groups WHERE enabled=1'):
            if g['role']=='portal' and not permanent: continue
            self.enqueue('banChatMember',dict(chat_id=g['chat'],user_id=uid,revoke_messages=True,_if_banned=True))

    def join_request(self,q):
        uid=q['from']['id']; g=self.db.one('SELECT * FROM groups WHERE chat=?',(q['chat']['id'],))
        if not g: return
        link=self.db.one('SELECT * FROM links WHERE url=?',(q.get('invite_link',{}).get('invite_link',''),))
        allowed=bool(g['enabled'] and link and link['uid']==uid and link['expires']>time.time() and self.entitled(uid,g))
        if g['role']=='proof' and not allowed and link and link['uid']==uid and link['expires']>time.time() and g['enabled'] and not self.user(uid)['banned']:
            used=self.db.one('SELECT 1 FROM proof_used WHERE uid=? AND gid=?',(uid,g['id']))
            allowed=not used
            if allowed:
                # Reserve once, then give a temporary entitlement; a retry can approve the same request.
                with self.db.transaction():
                    self.db.run('INSERT INTO proof_used(uid,gid) VALUES (?,?)',(uid,g['id']))
                    self.grant(uid,g,'proof',time.time()+600)
        self.api.call('approveChatJoinRequest' if allowed else 'declineChatJoinRequest',chat_id=g['chat'],user_id=uid)

    def member(self,q):
        g=self.db.one('SELECT * FROM groups WHERE chat=?',(q['chat']['id'],))
        if not g or not g['enabled']: return
        u=q['new_chat_member']['user']; uid=u['id']
        if u.get('is_bot'): return
        present=member_present(q['new_chat_member']); old=member_present(q['old_chat_member'])
        if present==old: return
        self.user(uid)
        if present:
            self.db.run('INSERT INTO members(uid,gid,joined,media) VALUES (?,?,?,?) ON CONFLICT(uid,gid) DO UPDATE SET present=1,joined=excluded.joined,media=excluded.media,warned=0',
                        (uid,g['id'],time.time(),time.time()))
            if q['new_chat_member']['status'] in ('administrator','creator'): return
            if not self.entitled(uid,g):
                self.enqueue('banChatMember',dict(chat_id=g['chat'],user_id=uid)); return
            if g['role']=='portal':
                link=self.db.one('SELECT * FROM links WHERE url=? AND purpose=?',
                                 (q.get('invite_link',{}).get('invite_link',''),'referral'))
                if link and link['uid']!=uid and link['expires']>time.time():
                    self.db.run('INSERT INTO referrals(invitee,inviter,joined) VALUES (?,?,?) ON CONFLICT(invitee) DO UPDATE SET present=1,joined=excluded.joined',
                                (uid,link['uid'],time.time()))
                else:
                    self.db.run('UPDATE referrals SET present=1,joined=? WHERE invitee=?',(time.time(),uid))
            elif g['role'] in ('chat','always'):
                self.enqueue('sendMessage',dict(chat_id=uid,text='Bienvenue ! Partage régulièrement des photos ou vidéos pour conserver ton accès.'))
        else:
            self.db.run('UPDATE members SET present=0 WHERE uid=? AND gid=?',(uid,g['id']))
            if g['role']=='portal': self.db.run('UPDATE referrals SET present=0 WHERE invitee=?',(uid,))

    def group_message(self,m):
        g=self.db.one('SELECT * FROM groups WHERE chat=?',(m['chat']['id'],))
        if not g or not g['enabled']: return
        uid=m.get('from',{}).get('id',0); mid=m['message_id']
        media=m.get('photo',[{}])[-1] if m.get('photo') else m.get('video',{})
        fid=media.get('file_unique_id')
        previous=self.db.one('SELECT * FROM messages WHERE gid=? AND mid=?',(g['id'],mid))
        self.db.run('INSERT INTO messages(gid,mid,uid,album,file_id,created) VALUES (?,?,?,?,?,?) ON CONFLICT(gid,mid) DO NOTHING',
                    (g['id'],mid,uid,m.get('media_group_id'),fid,m.get('date',time.time())))
        if m.get('new_chat_members') or m.get('left_chat_member'):
            self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=mid)); return
        text=m.get('text',m.get('caption',''))
        is_admin=self.admin(uid,g) if uid else False
        reply=m.get('reply_to_message')
        if text.split(' ')[0].split('@')[0]=='/pedo' and is_admin and reply:
            victim=reply.get('from',{}).get('id')
            if not victim or self.admin(victim,g): return
            self.ban(victim,True)
            album=reply.get('media_group_id')
            items=self.db.all('SELECT * FROM messages WHERE gid=? AND '+('album=?' if album else 'mid=?'),(g['id'],album or reply['message_id']))
            for item in items:
                self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=item['mid']))
                if item['file_id']:
                    self.db.run('INSERT INTO fingerprints(file_id,blocked) VALUES (?,1) ON CONFLICT(file_id) DO UPDATE SET blocked=1',(item['file_id'],))
            self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=mid)); return
        if is_admin: return
        if self.user(uid)['permanent'] or (self.user(uid)['banned'] and g['role']!='portal'):
            self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=mid)); return
        if g['role']=='portal': return
        if forbidden(text,self.db.setting('words').splitlines()):
            row=self.db.one('SELECT count FROM strikes WHERE uid=?',(uid,))
            n=(row['count'] if row else 0)+1
            self.db.run('INSERT INTO strikes(uid,count) VALUES (?,?) ON CONFLICT(uid) DO UPDATE SET count=excluded.count',(uid,n))
            self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=mid))
            if n>=3: self.ban(uid)
            else: self.enqueue('restrictChatMember',dict(chat_id=g['chat'],user_id=uid,permissions={'can_send_messages':False},until_date=int(time.time()+(86400 if n==1 else 4*86400))))
            return
        if reply and '@admin' in text.casefold():
            victim=reply.get('from',{}).get('id')
            if victim and victim!=uid and not self.admin(victim,g):
                target=str(reply.get('media_group_id') or reply['message_id'])
                self.db.run('INSERT INTO reports(gid,target,reporter) VALUES (?,?,?) ON CONFLICT DO NOTHING',(g['id'],target,uid))
                if self.db.one('SELECT COUNT(*) AS n FROM reports WHERE gid=? AND target=?',(g['id'],target))['n']>=4:
                    self.ban(victim)
        if fid and not (previous and previous['file_id']==fid):
            blocked=self.db.one('SELECT 1 FROM fingerprints WHERE file_id=? AND blocked=1',(fid,))
            duplicate=self.db.one('SELECT 1 FROM seen_media WHERE gid=? AND file_id=?',(g['id'],fid))
            if blocked or (g['anti'] and duplicate):
                self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=mid)); return
            self.db.run('INSERT INTO media_tasks(id,gid,mid,file_id,download_id,kind,due) VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                        (g['id']+':'+str(mid),g['id'],mid,fid,media['file_id'],'photo' if m.get('photo') else 'video',time.time()))
            self.db.run('INSERT INTO seen_media(gid,file_id) VALUES (?,?) ON CONFLICT DO NOTHING',(g['id'],fid))
            self.db.run('UPDATE members SET media=?,warned=0 WHERE uid=? AND gid=?',(time.time(),uid,g['id']))
        elif not fid and not m.get('text'):
            self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=mid))

    def update(self,u):
        if 'pre_checkout_query' in u: return self.checkout(u['pre_checkout_query'])
        if 'callback_query' in u: return self.callback(u['callback_query'])
        if 'chat_join_request' in u: return self.join_request(u['chat_join_request'])
        if 'chat_member' in u: return self.member(u['chat_member'])
        if 'my_chat_member' in u:
            q=u['my_chat_member']; chat=q['chat']
            if chat['type'] not in ('group','supergroup'): return
            if q['new_chat_member']['status']=='administrator':
                self.db.run('INSERT INTO groups(id,chat,title) VALUES (?,?,?) ON CONFLICT(chat) DO NOTHING',
                            (uuid.uuid4().hex[:12],chat['id'],chat.get('title','Groupe')))
                self.alert('Groupe à configurer : '+chat.get('title','Groupe'))
            else: self.alert('Bot retiré ou permissions modifiées : '+chat.get('title','Groupe'))
            return
        m=u.get('message') or u.get('edited_message')
        if not m: return
        if m['chat']['type']!='private': return self.group_message(m)
        uid=m['from']['id']; user=self.user(uid)
        if m.get('successful_payment'): return self.paid(uid,m['successful_payment'])
        text=m.get('text','')
        if text.startswith('/paysupport') or text.startswith('/support'):
            return self.screen(uid,'Assistance : '+html.escape(self.support),[[button('⬅️ Accueil','home')]])
        if text.startswith('/start'):
            self.db.run('UPDATE users SET pending=NULL WHERE id=?',(uid,))
            arg=text.partition(' ')[2]
            return self.show(uid,'g:'+arg[2:] if arg.startswith('g_') else ('invite' if arg=='invite' else 'home'))
        if user['pending']: return self.input(uid,m,user['pending'])
        return self.show(uid)

    def permissions(self,g,opened):
        self.api.call('setChatPermissions',chat_id=g['chat'],use_independent_chat_permissions=True,
            permissions={'can_send_messages':opened,'can_send_photos':opened,'can_send_videos':opened,
                         'can_send_audios':False,'can_send_documents':False,'can_send_voice_notes':False,
                         'can_send_video_notes':False,'can_send_polls':False,'can_send_other_messages':False,
                         'can_add_web_page_previews':False,'can_invite_users':False,
                         'can_change_info':False,'can_pin_messages':False})

    def notice(self,g,kind,text,rows):
        old=self.db.one('SELECT * FROM notices WHERE gid=? AND kind=?',(g['id'],kind))
        body=json.dumps([text,rows],ensure_ascii=False)
        if old and old['body']==body: return
        args=dict(chat_id=g['chat'],text=text,reply_markup=keyboard(*rows))
        if old:
            try:
                self.api.call('editMessageText',message_id=old['mid'],**args)
                self.db.run('UPDATE notices SET body=? WHERE gid=? AND kind=?',(body,g['id'],kind)); return
            except APIError as e:
                if 'message is not modified' in str(e): return
                if 'message to edit not found' not in str(e): raise
        sent=self.api.call('sendMessage',**args)
        self.db.run('INSERT INTO notices(gid,kind,mid,body) VALUES (?,?,?,?) ON CONFLICT(gid,kind) DO UPDATE SET mid=excluded.mid,body=excluded.body',
                    (g['id'],kind,sent['message_id'],body))

    def remove_notice(self,g,kind):
        old=self.db.one('SELECT * FROM notices WHERE gid=? AND kind=?',(g['id'],kind))
        if old:
            self.enqueue('deleteMessage',dict(chat_id=g['chat'],message_id=old['mid']))
            self.db.run('DELETE FROM notices WHERE gid=? AND kind=?',(g['id'],kind))

    def jobs(self):
        for job in self.db.all('SELECT * FROM jobs WHERE due<=? ORDER BY due LIMIT 30',(time.time(),)):
            try:
                payload=json.loads(job['payload'])
                guard_gid=payload.pop('_unless_entitled',None)
                guard_ban=payload.pop('_if_banned',False)
                guard_unban=payload.pop('_unless_banned',False)
                skip=(guard_gid and self.entitled(payload['user_id'],self.group(guard_gid)))
                if guard_ban: skip=not self.user(payload['user_id'])['banned']
                if guard_unban: skip=bool(self.user(payload['user_id'])['banned'])
                if not skip: self.api.call(job['method'],**payload)
                self.db.run('DELETE FROM jobs WHERE id=?',(job['id'],))
            except APIError as e:
                # A missing message is already deleted. Other failures remain visible/retriable.
                if job['method']=='deleteMessage' and 'message to delete not found' in str(e):
                    self.db.run('DELETE FROM jobs WHERE id=?',(job['id'],)); continue
                attempts=job['attempts']+1
                delay=max(e.retry,min(3600,2**min(attempts,12)))
                self.db.run('UPDATE jobs SET attempts=?,due=? WHERE id=?',(attempts,time.time()+delay,job['id']))
                if attempts==3 and job['method']!='sendMessage': self.alert('Opération à vérifier : '+job['method']+' — '+str(e))

    def close(self,g):
        # Lock first, enqueue all cleanup transactionally, then record closure.
        self.permissions(g,False)
        with self.db.transaction():
            items=self.db.all('SELECT mid FROM messages WHERE gid=?',(g['id'],))
            items += self.db.all('SELECT mid FROM notices WHERE gid=?',(g['id'],))
            mids=sorted({r['mid'] for r in items})
            for start in range(0,len(mids),100):
                self.enqueue('deleteMessages',dict(chat_id=g['chat'],message_ids=mids[start:start+100]))
            self.db.run('DELETE FROM messages WHERE gid=?',(g['id'],))
            self.db.run('DELETE FROM notices WHERE gid=?',(g['id'],))
            self.db.run('UPDATE groups SET opened=0 WHERE id=?',(g['id'],))

    def scheduled(self,groups,now):
        local=datetime.fromtimestamp(now,ZoneInfo(self.tz))
        candidates=[g for g in groups if g['role']=='chat']
        windows={}
        for day in (local.date()-timedelta(days=1),local.date(),local.date()+timedelta(days=1)):
            key=day.isoformat()
            session=self.db.one('SELECT * FROM sessions WHERE day=?',(key,))
            if not session and candidates and day>=local.date():
                previous=self.db.one('SELECT gid FROM sessions WHERE day<? ORDER BY day DESC LIMIT 1',(key,))
                pool=[g for g in candidates if not previous or g['id']!=previous['gid']] or candidates
                selected=min(pool,key=lambda g:(g['last_open'],g['id']))
                self.db.run('INSERT INTO sessions(day,gid) VALUES (?,?) ON CONFLICT DO NOTHING',(key,selected['id']))
                session={'gid':selected['id']}
            if session:
                selected=next((g for g in candidates if g['id']==session['gid']),None)
                if selected:
                    a,b=window(day,selected['slot'],self.tz)
                    if b>now: windows.setdefault(selected['id'],[]).append((a,b))
        for g in groups:
            if g['role']=='free':
                for delta in range(-1,8):
                    day=local.date()+timedelta(days=delta)
                    if day.weekday()==g['weekday']:
                        a,b=window(day,g['slot'],self.tz)
                        if b>now: windows.setdefault(g['id'],[]).append((a,b))
        return windows

    def tick(self):
        now=time.time()
        self.jobs()
        if now-self.last_tick<20: return
        self.last_tick=now
        warning=self.db.setting('media_warning')
        if warning:
            self.alert(warning); self.db.set('media_warning','')
        # Confirm current membership instead of relying solely on possibly missed leave updates.
        portal=self.db.one("SELECT * FROM groups WHERE role='portal' AND enabled=1")
        if portal:
            for r in self.db.all('SELECT * FROM referrals WHERE valid=0 AND present=1 AND joined<=? LIMIT 100',(now-300,)):
                member=self.api.call('getChatMember',chat_id=portal['chat'],user_id=r['invitee'])
                if not member_present(member):
                    self.db.run('UPDATE referrals SET present=0 WHERE invitee=?',(r['invitee'],)); continue
                if self.db.setting('referral_rule','simple')=='cascade':
                    # Child must have stayed 5 min, not be recursively validated (avoids deadlock).
                    children=self.db.all('SELECT * FROM referrals WHERE inviter=? AND present=1 AND joined<=?',(r['invitee'],now-300))
                    qualifies=False
                    for child in children:
                        if member_present(self.api.call('getChatMember',chat_id=portal['chat'],user_id=child['invitee'])):
                            qualifies=True; break
                    if not qualifies: continue
                self.db.run('UPDATE referrals SET valid=1 WHERE invitee=?',(r['invitee'],))
        for a in self.db.all('SELECT * FROM access WHERE revoked=0 AND expires IS NOT NULL AND expires<=?',(now,)):
            g=self.group(a['gid'])
            if g:
                with self.db.transaction():
                    self.enqueue('banChatMember',dict(chat_id=g['chat'],user_id=a['uid'],_unless_entitled=g['id']))
                    self.db.run('UPDATE access SET revoked=1 WHERE uid=? AND gid=?',(a['uid'],a['gid']))
        self.db.run("UPDATE orders SET status='expired' WHERE (status='pending' AND created<?) OR (status='checkout' AND created<?)",(now-3600,now-900))
        groups=self.db.all('SELECT * FROM groups WHERE enabled=1')
        windows=self.scheduled(groups,now)
        # Close all outdated sessions BEFORE opening another, even across midnight.
        for g in groups:
            spans=windows.get(g['id'],[])
            should_open=g['role'] in ('always','vip','proof') or any(a<=now<b for a,b in spans)
            if g['role']=='portal': continue
            if g['opened'] and not should_open:
                self.close(g); g['opened']=0
        for g in groups:
            try:
                spans=windows.get(g['id'],[])
                active=next(((a,b) for a,b in spans if a<=now<b),None)
                should_open=g['role'] in ('always','vip','proof') or bool(active)
                count=None
                if should_open and not g['opened'] and g['locker']:
                    # Telegram authoritative count, minus bot. No claim that members are active.
                    count=max(0,self.api.call('getChatMemberCount',chat_id=g['chat'])-1)
                    should_open=count>=g['threshold']
                if g['role']!='portal':
                    if should_open and not g['opened']:
                        # Admin permissions are unaffected by setChatPermissions.
                        self.permissions(g,True)
                        self.db.run('UPDATE groups SET opened=1,last_open=? WHERE id=?',(now,g['id']))
                        g['opened']=1
                    elif not g['opened'] and not self.db.setting('locked:'+g['id']):
                        self.permissions(g,False); self.db.set('locked:'+g['id'],'1')
                    invite=[button('🔗 J’invite',url=self.deep('invite'))]
                    if count is not None and count<g['threshold']:
                        self.notice(g,'status',f'🔒 Le groupe démarrera avec au moins {g["threshold"]} membres présents, pendant son créneau.\n\n'+progress(count,g['threshold']),[invite])
                    elif g['opened'] and active:
                        remaining=active[1]-now
                        message='🟢 Groupe ouvert, vous pouvez envoyer textes, photos et vidéos.'
                        if remaining<=1800: message+='\nFermeture dans '+countdown(remaining)+'.'
                        self.notice(g,'status',message,[invite])
                    elif not g['opened']:
                        future=[a for a,b in spans if a>now]
                        message='🔴 Groupe fermé.'+(' Ouverture dans '+countdown(min(future)-now)+'.' if future else ' Prochaine ouverture annoncée ici.')
                        self.notice(g,'status',message,[invite])
                    if g['opened'] and g['rules']:
                        bucket=str(int(now//1800))
                        if self.db.setting('rules:'+g['id'])!=bucket:
                            self.remove_notice(g,'rules')
                            self.notice(g,'rules',g['rules'],[])
                            self.db.set('rules:'+g['id'],bucket)
                if now-float(self.db.setting('health:'+g['id'],'0'))>=7200:
                    self.check_rights(g)
                    self.db.set('health:'+g['id'],now)
            except (APIError,ValueError) as e:
                self.alert(g['title']+' : '+str(e))
        # Public portal & temporary cross-promotion, maintained in one message per group.
        live=self.db.one("SELECT * FROM groups WHERE enabled=1 AND role='chat' AND opened=1")
        for g in groups:
            if g['role']=='portal':
                text=g['description'] or '✨ Bienvenue ! Découvre les groupes et débloque tes accès.'
                if live: text+='\n\n🔥 '+live['title']+' est ouvert !'
                rows=[[button('👤 Découvrir les groupes',url=self.deep())]]
                if live: rows.insert(0,[button('🔥 '+live['title'],url=self.deep('g_'+live['id']))])
                self.notice(g,'portal',text,rows)
            elif g['role'] in ('chat','always','free'):
                if live and g['id']!=live['id']:
                    self.notice(g,'live','🔥 '+live['title']+' est ouvert !',
                        [[button('👀 Découvrir',url=self.deep('g_'+live['id']))],[button('🔗 J’invite',url=self.deep('invite'))]])
                else: self.remove_notice(g,'live')
        for m in self.db.all("SELECT m.* FROM members m JOIN groups g ON m.gid=g.id WHERE m.present=1 AND g.enabled=1 AND g.role IN ('chat','always') AND m.media<=?",(now-3*86400,)):
            g=self.group(m['gid'])
            if self.admin(m['uid'],g): continue
            if m['media']<=now-4*86400:
                with self.db.transaction():
                    self.enqueue('banChatMember',dict(chat_id=g['chat'],user_id=m['uid'],_unless_entitled=g['id']))
                    self.db.run('UPDATE members SET present=0 WHERE uid=? AND gid=?',(m['uid'],g['id']))
                    self.db.run('UPDATE access SET revoked=1,base=? WHERE uid=? AND gid=?',(self.count(m['uid']),m['uid'],g['id']))
            elif not m['warned']:
                self.enqueue('sendMessage',dict(chat_id=m['uid'],text='⚠️ Partage un média dans '+g['title']+' avant demain pour conserver ton accès.'))
                self.db.run('UPDATE members SET warned=1 WHERE uid=? AND gid=?',(m['uid'],g['id']))
        advertised=set()
        for ad in self.db.all('SELECT a.* FROM ads a JOIN groups g ON a.gid=g.id WHERE a.enabled=1 AND g.enabled=1 AND g.opened=1 AND a.next_at<=? ORDER BY a.next_at,a.id',(now,)):
            if ad['gid'] in advertised: continue
            advertised.add(ad['gid'])
            g=self.group(ad['gid']); self.remove_notice(g,'ad')
            if ad['photo']:
                sent=self.api.call('sendPhoto',chat_id=g['chat'],photo=ad['photo'],caption=ad['text'])
            else: sent=self.api.call('sendMessage',chat_id=g['chat'],text=ad['text'])
            self.db.run("INSERT INTO notices(gid,kind,mid,body) VALUES (?,'ad',?,?) ON CONFLICT(gid,kind) DO UPDATE SET mid=excluded.mid,body=excluded.body",(g['id'],sent['message_id'],ad['text']))
            # Rotate campaigns within the group, one visible ad at a time.
            self.db.run('UPDATE ads SET next_at=? WHERE gid=?',(now+ad['interval_hours']*3600,g['id']))
            self.db.run('UPDATE ads SET next_at=? WHERE id=?',(now+(ad['interval_hours']*3600)+1,ad['id']))
        self.jobs()
