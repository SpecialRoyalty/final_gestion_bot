import json
from datetime import datetime
from urllib.parse import urlencode
from .domain import ROLES,SLOTS,config,now,progress
from .telegram import TelegramError


def b(text,data):return {'text':text,'callback_data':data}
def url(text,link):return {'text':text,'url':link}

class UI:
    def __init__(self,s,scheduler,username):self.s=s;self.sch=scheduler;self.username=username

    def show(self,uid,text,rows,screen='home'):
        db=self.s.db
        user=db.one('SELECT * FROM users WHERE id=?',(uid,))
        if not user:return
        markup={'inline_keyboard':rows}
        data=dict(chat_id=uid,text=text[:4096],reply_markup=markup)
        if user['menu_id']:
            try:
                self.s.tg.call('editMessageText',message_id=user['menu_id'],**data)
            except TelegramError as e:
                if 'message is not modified' in e.description:pass
                elif 'message to edit not found' in e.description:
                    result=self.s.tg.call('sendMessage',**data)
                    db.run('UPDATE users SET menu_id=? WHERE id=?',(result['message_id'],uid))
                else:raise
        else:
            result=self.s.tg.call('sendMessage',**data)
            db.run('UPDATE users SET menu_id=? WHERE id=?',(result['message_id'],uid))
        db.run('UPDATE users SET screen=? WHERE id=?',(screen,uid))

    def nav(self,screen,back='home'):
        return [[b('🔄 Actualiser',screen)],[b('⬅️ Retour',back),b('🏠 Accueil','home')]]

    def render(self,uid,screen='home'):
        s=self.s;db=s.db
        ban=s.banned(uid)
        if ban and uid not in s.owners:
            if ban['permanent']:return self.show(uid,'Accès indisponible.',[])
            if screen not in ('unban','invite'):
                return self.show(uid,'🔒 Ton accès est suspendu.',[[b('🔓 Demande de déban','unban')]])
        if screen=='home':
            groups=db.all("SELECT * FROM groups WHERE role NOT IN ('portal','pending') ORDER BY role,name")
            rows=[]
            for g in groups:
                if not config(g)['enabled']:continue
                label='Groupe chat gratuit' if g['role']=='free' else g['name']
                if s.owned(uid,g['id']):label+=' · ✅'
                rows.append([b(label,'g:'+g['id'])])
            rows.extend([[b('🎯 Mes défis','challenges'),b('👤 Mes accès','access')],[b('🛒 Mon panier','cart'),b('📨 J’invite','invite')],[b('🔄 Actualiser','home'),b('🆘 Aide','help')]])
            if uid in s.owners or any(s.admin(uid,g['id']) for g in db.all('SELECT * FROM groups')):
                rows.append([b('⚙️ Administration','admin')])
            name=db.one('SELECT name FROM users WHERE id=?',(uid,))['name']
            opened=db.one("SELECT g.name FROM groups g JOIN sessions x ON x.gid=g.id WHERE x.state='open' AND x.ends>?",(now(),))
            text=f'🌙 Bonjour {name} !\n\nChoisis ton espace.'
            if opened:text+='\n\n🔥 '+opened['name']+' est ouvert !'
            if not groups:text+='\n\nLes groupes seront disponibles prochainement.'
            return self.show(uid,text,rows,'home')
        if screen.startswith('g:'):
            gid=screen[2:];g=s.group(gid);c=config(g);rows=[]
            label='Groupe chat gratuit' if g['role']=='free' else g['name']
            text=label+'\n\n'+c['description']
            if not g['healthy'] or not c['enabled']:
                text+='\n\n⏳ Groupe temporairement indisponible. Tes droits sont conservés.'
            elif s.owned(uid,gid):
                a=db.one('SELECT * FROM access WHERE uid=? AND gid=?',(uid,gid))
                text+='\n\n✅ Accès actif'
                if a['expires']:text+=' jusqu’au '+datetime.fromtimestamp(a['expires']).strftime('%d/%m/%Y')
                rows.append([b('🚪 Rejoindre','join:'+gid)])
            elif g['role'] in ('proof','free'):
                if g['role']=='proof':text+='\n\nVisite unique de 10 minutes, non renouvelable.'
                rows.append([b('🚪 Rejoindre','join:'+gid)])
            else:
                if g['role'] in ('chat','always'):
                    ch=db.one('SELECT * FROM challenges WHERE uid=? AND gid=?',(uid,gid))
                    text+='\n\n🎯 '+progress(s.count(uid,gid),c['goal'])
                    rows.append([b('📨 J’invite' if ch else '🎯 Commencer mon défi','invite' if ch else 'challenge:'+gid)])
                if c['price']:
                    text+=f'\n\n⭐ {c["price"]} Stars · '+('30 jours' if c['monthly'] else 'paiement unique')
                    rows.append([b('🛒 Ajouter au panier','add:'+gid)])
                text+='\n\n🔐 Ton accès acquis est conservé si ce groupe est remplacé.'
            if c['locker']:
                text+=f'\n\n🔒 Seuil de lancement : {g["member_count"]}/{c["threshold"]} membres présents. L’achat ne contourne pas le Locker.'
            if g['role'] in ('chat','free'):
                a,z=SLOTS[c['slot']];text+=f'\n🕒 Créneau : {a:02d} h–{z:02d} h ({self.sch.tz})'
            return self.show(uid,text,rows+self.nav(screen),screen)
        if screen in ('access','challenges'):
            rows=[];lines=[]
            for g in db.all("SELECT * FROM groups WHERE role NOT IN ('portal','pending')"):
                if screen=='access' and s.owned(uid,g['id']):rows.append([b('🚪 '+g['name'],'g:'+g['id'])])
                if screen=='challenges' and db.one('SELECT uid FROM challenges WHERE uid=? AND gid=?',(uid,g['id'])):
                    lines.append(g['name']+' : '+progress(s.count(uid,g['id']),config(g)['goal']))
                    rows.append([b(g['name'],'g:'+g['id'])])
            return self.show(uid,('👤 Mes accès' if screen=='access' else '🎯 Mes défis')+'\n\n'+'\n'.join(lines),rows+self.nav(screen),screen)
        if screen=='invite':
            link=s.referral_link(uid)
            return self.show(uid,'📨 Invite tes amis\n\nTon lien personnel mène au portail :\n'+link,[[url('📤 Partager mon lien','https://t.me/share/url?'+urlencode({'url':link,'text':'Rejoins notre communauté !'}))]]+self.nav(screen),screen)
        if screen=='cart':
            # Drop owned items unless a payment is actively completing.
            order=db.one("SELECT * FROM orders WHERE uid=? AND status IN ('pending','checkout')",(uid,))
            if not order:
                for item in db.all('SELECT gid FROM cart WHERE uid=?',(uid,)):
                    if s.owned(uid,item['gid']):db.run('DELETE FROM cart WHERE uid=? AND gid=?',(uid,item['gid']))
            gs=db.all('SELECT g.* FROM groups g JOIN cart c ON c.gid=g.id WHERE c.uid=?',(uid,))
            total=sum(config(g)['price'] for g in gs)
            lines=[f'• {g["name"]} : {config(g)["price"]} ⭐ — '+('30 jours' if config(g)['monthly'] else 'unique') for g in gs]
            rows=[[b('❌ '+g['name'],'remove:'+g['id'])] for g in gs]
            if gs:rows.append([b(f'💳 Payer {total} Stars','checkout')])
            rows.append([b('🗑 Vider et annuler','empty')])
            return self.show(uid,'🛒 Mon panier\n\n'+'\n'.join(lines)+f'\n\nTotal : {total} Stars\nAbonnements de 30 jours à renouvellement manuel.',rows+self.nav(screen),screen)
        if screen=='checkout':
            o=s.checkout(uid)
            if not o['invoice']:
                link=s.tg.call('createInvoiceLink',title='Accès à la communauté',description='Accès sélectionnés. Les formules de 30 jours se renouvellent manuellement.',payload=o['id'],provider_token='',currency='XTR',prices=[{'label':'Panier','amount':o['total']}])
                db.run('UPDATE orders SET invoice=? WHERE id=?',(link,o['id']));o['invoice']=link
            return self.show(uid,f'💳 Finaliser mon panier\n\nTotal : {o["total"]} Stars\n\nEn poursuivant, tu acceptes les conditions :\n'+db.setting('terms',''),[[url('⭐ Ouvrir le paiement',o['invoice'])],[b('✕ Annuler','cancel')]]+self.nav('cart'),screen)
        if screen=='unban':
            db.run("INSERT INTO challenges VALUES('unban',?,?) ON CONFLICT DO NOTHING",(uid,now()))
            return self.show(uid,'🔓 Demande de retour\n\n'+progress(s.count(uid,'unban'),db.setting('unban_goal',15)),[[b('📨 J’invite','invite')]]+self.nav(screen),screen)
        if screen=='help':
            return self.show(uid,'🆘 Aide et paiements\n\n'+db.setting('support','Contacte un administrateur du portail.')+'\n\nConditions :\n'+db.setting('terms','Non renseignées.'),self.nav('help'),'help')
        if screen=='admin':
            gs=[g for g in db.all('SELECT * FROM groups') if s.admin(uid,g['id'])]
            rows=[[b(('⚪ ' if g['role']=='pending' else '🟢 ')+g['name'],'ag:'+g['id'])] for g in gs]
            if uid in s.owners:
                rows += [[b('📜 Règles communes','setting:rules'),b('🛡 Mots interdits','setting:words')],[b('🎯 Validation invitations','strict'),b('🔓 Objectif déban','setting:unban_goal')],[b('📢 Texte portail','setting:portal_text')],[b('🆘 Support','setting:support'),b('📃 Conditions de vente','setting:terms')],[b('💳 Ventes ON/OFF','sales'),b('🚨 Alertes','alerts')],[b('👥 Débannir un membre','setting:unban_user'),b('🎟 Accorder un accès','setting:grant_user')],[b('↩️ Rembourser une commande','setting:refund')]]
            return self.show(uid,'⚙️ Administration\n\nAjoute le bot comme administrateur à un supergroupe, puis choisis son rôle ici.\n\nVentes : '+('ON' if db.setting('sales',False) else 'OFF'),rows+self.nav('admin'),'admin')
        if screen=='alerts':
            s.require_admin(uid)
            alerts=db.all('SELECT * FROM alerts WHERE resolved=0 ORDER BY created DESC LIMIT 15')
            return self.show(uid,'🚨 Alertes\n\n'+'\n\n'.join(r['body'] for r in alerts),self.nav(screen,'admin'),screen)
        if screen.startswith('ag:'):
            gid=screen[3:];s.require_admin(uid,gid);g=s.group(gid);c=config(g)
            rows=[]
            if g['role']=='pending':
                # Network owners alone assign network roles; local admins can manage existing settings.
                if uid in s.owners:rows += [[b(label,'role:'+gid+':'+role)] for role,label in ROLES.items()]
            else:
                for field,label in [('description','Description'),('goal','Objectif invitations'),('price','Prix en Stars'),('threshold','Seuil Locker'),('rules','Règles'),('ads','Publicités simples'),('vip_ads','Publicités VIP')]:
                    rows.append([b('✏️ '+label,'edit:'+gid+':'+field)])
                for field,label in [('enabled','Disponible'),('locker','Locker'),('dedup','Anti-doublons'),('inactivity','Inactivité 3/4 jours'),('reports','Ban après 4 signalements')]:
                    if field=='locker' and g['role'] not in ('chat','always','free','vip'):continue
                    rows.append([b(label+' : '+('ON' if c[field] else 'OFF'),'toggle:'+gid+':'+field)])
                if g['role']=='vip':rows.append([b('Formule : '+('30 jours' if c['monthly'] else 'unique'),'toggle:'+gid+':monthly')])
                rows.append([b('🕒 Horaires','slots:'+gid)])
                if g['role']=='free':rows.append([b('📅 Jour de la semaine','edit:'+gid+':weekday')])
                if uid in s.owners:rows.append([b('🔄 Remplacer ce groupe','replace:'+gid)])
                if g['role']=='portal':rows.append([b('📢 Publier le portail','publish:'+gid)])
            rows.append([b('🔍 Vérifier les permissions','health:'+gid)])
            return self.show(uid,'⚙️ '+g['name']+'\nIdentifiant : '+gid+'\nRôle : '+ROLES.get(g['role'],'À configurer')+f'\nMembres présents (hors bot) : {g["member_count"]}\nPermissions : '+('OK' if g['healthy'] else 'à vérifier'),rows+self.nav(screen,'admin'),screen)
        raise ValueError('Écran inconnu. Reviens à l’accueil.')

    def action(self,uid,data):
        s=self.s;db=s.db
        if data.startswith('a:'):raise ValueError('Ancien bouton : actualise le menu.')
        if s.banned(uid) and uid not in s.owners and data not in ('home','unban','invite'):
            return self.render(uid,'home')
        if data.startswith('challenge:'):
            gid=data.split(':')[1];s.challenge(uid,gid);return self.render(uid,'g:'+gid)
        if data.startswith('join:'):
            gid=data.split(':')[1];link=s.entry_link(uid,gid)
            return self.show(uid,'🚪 Ton lien personnel est prêt.\nValable 10 minutes. Seul ton compte peut être accepté.',[[url('Rejoindre le groupe',link)]]+self.nav('g:'+gid),'g:'+gid)
        if data.startswith(('add:','remove:')):
            verb,gid=data.split(':');s.cancel_order(uid)
            g=s.group(gid)
            if verb=='add':
                if s.owned(uid,gid):raise ValueError('Tu possèdes déjà cet accès.')
                if g['role'] not in ('chat','always','vip') or config(g)['price']<=0:raise ValueError('Accès non commercialisé.')
                db.run('INSERT INTO cart VALUES(?,?) ON CONFLICT DO NOTHING',(uid,gid))
            else:db.run('DELETE FROM cart WHERE uid=? AND gid=?',(uid,gid))
            return self.render(uid,'cart')
        if data in ('empty','cancel'):
            s.cancel_order(uid)
            if data=='empty':db.run('DELETE FROM cart WHERE uid=?',(uid,))
            return self.render(uid,'cart')
        if data.startswith('role:'):
            _,gid,role=data.split(':');s.require_admin(uid);s.require_admin(uid,gid)
            if role not in ROLES:raise ValueError('Rôle invalide.')
            if role=='portal' and db.one("SELECT id FROM groups WHERE role='portal' AND id!=?",(gid,)):raise ValueError('Un portail existe déjà : utilise Remplacer.')
            if not s.health(s.group(gid)):raise ValueError('Accorde les permissions nécessaires avant de continuer.')
            db.run('UPDATE groups SET role=? WHERE id=?',(role,gid))
            self.sch.set_open(s.group(gid),role=='proof')
            s.audit(uid,'role',gid+':'+role)
            for banned in db.all('SELECT * FROM bans'):
                if role!='portal' or banned['permanent']:s.enqueue(f'ban:{gid}:{banned["uid"]}','ban',{'gid':gid,'uid':banned['uid']})
            if role=='portal':self.publish(s.group(gid))
            return self.render(uid,'ag:'+gid)
        if data.startswith('toggle:'):
            _,gid,field=data.split(':');s.require_admin(uid,gid)
            if field not in ('enabled','locker','dedup','inactivity','reports','monthly'):raise ValueError('Réglage inconnu.')
            if field=='monthly':s.require_admin(uid)
            g=s.group(gid);c=config(g);c[field]=not c[field]
            db.run('UPDATE groups SET cfg=? WHERE id=?',(json.dumps(c),gid));s.audit(uid,'toggle',data)
            return self.render(uid,'ag:'+gid)
        if data.startswith('health:'):
            gid=data.split(':')[1];s.require_admin(uid,gid);s.health(s.group(gid));return self.render(uid,'ag:'+gid)
        if data.startswith('slots:'):
            gid=data.split(':')[1];s.require_admin(uid,gid)
            return self.show(uid,'🕒 Choisis le créneau\nModification appliquée aux prochaines sessions.',[[b(f'{a:02d} h–{z:02d} h',f'slot:{gid}:{i}')] for i,(a,z) in enumerate(SLOTS)]+self.nav('ag:'+gid),'ag:'+gid)
        if data.startswith('slot:'):
            _,gid,index=data.split(':');s.require_admin(uid,gid);c=config(s.group(gid));c['slot']=int(index)
            if c['slot'] not in range(len(SLOTS)):raise ValueError('Horaire invalide.')
            db.run('UPDATE groups SET cfg=? WHERE id=?',(json.dumps(c),gid));return self.render(uid,'ag:'+gid)
        if data.startswith(('edit:','setting:')):
            pieces=data.split(':')
            if pieces[0]=='edit':s.require_admin(uid,pieces[1])
            else:s.require_admin(uid)
            field=pieces[-1]
            if field in ('price','monthly'):s.require_admin(uid)
            help_text={'ads':'Envoie un tableau JSON : [{"text":"Ta publicité","photo":"FILE_ID_OPTIONNEL","hours":4,"enabled":true}]. Intervalles : 2, 4, 6 h. Pour une photo, envoie-la ici et son identifiant sera affiché.','vip_ads':'Tableau JSON comme les publicités simples. Intervalles : 0 (une fois), 6 ou 12 h.','weekday':'0=lundi, 1=mardi, 2=mercredi, …, 6=dimanche.','words':'Un mot ou une expression par ligne.','refund':'Envoie l’identifiant de commande à rembourser. Cette action rembourse les Stars et retire les accès de cette commande.','grant_user':'Envoie l’identifiant Telegram et l’identifiant logique du groupe, séparés par un espace. Les identifiants des groupes figurent sur leur fiche admin. Accès permanent offert.', 'unban_user':'Envoie l’identifiant Telegram numérique. Un bannissement définitif ne peut pas être levé ici.'}.get(field,'Envoie la nouvelle valeur dans cette conversation.')
            db.run('UPDATE users SET wizard=? WHERE id=?',(data,uid))
            return self.show(uid,'✏️ '+field+'\n\n'+help_text,[[b('Annuler','abort')]],'admin')
        if data=='abort':
            db.run('UPDATE users SET wizard=NULL WHERE id=?',(uid,));return self.render(uid,'admin')
        if data.startswith('replace:'):
            s.require_admin(uid);gid=data.split(':')[1]
            rows=[[b(g['name'],'swap:'+gid+':'+g['id'])] for g in db.all("SELECT * FROM groups WHERE role='pending'")]
            return self.show(uid,'Choisis le nouveau groupe connecté. Les droits de l’ancien seront conservés.',rows+self.nav('ag:'+gid),'ag:'+gid)
        if data.startswith('swap:'):
            _,old,new=data.split(':');s.replace(uid,old,new);return self.render(uid,'ag:'+old)
        if data.startswith('publish:'):
            gid=data.split(':')[1];s.require_admin(uid,gid);self.publish(s.group(gid));return self.render(uid,'ag:'+gid)
        if data=='strict':
            s.require_admin(uid);db.set('ref_strict',not db.setting('ref_strict',False))
            return self.show(uid,'Validation interne : '+('5 minutes et un invité présent 5 minutes' if db.setting('ref_strict') else '5 minutes de présence'),self.nav('admin'),'admin')
        if data=='sales':
            s.require_admin(uid)
            if not db.setting('support') or not db.setting('terms'):raise ValueError('Renseigne le support et les conditions de vente avant d’activer les ventes.')
            db.set('sales',not db.setting('sales',False));return self.render(uid,'admin')
        return self.render(uid,data)

    def input(self,uid,m):
        s=self.s;db=s.db;u=db.one('SELECT * FROM users WHERE id=?',(uid,));wizard=u['wizard']
        if not wizard:return self.render(uid,u['screen'])
        p=wizard.split(':');field=p[-1];text=m.get('text','').strip()
        if p[0]=='edit':
            gid=p[1];s.require_admin(uid,gid);g=s.group(gid);c=config(g)
            allowed=('description','goal','price','threshold','rules','ads','vip_ads','weekday')
            if field not in allowed:raise ValueError('Champ inconnu.')
            if field=='price':s.require_admin(uid)
            if field in ('goal','price','threshold','weekday'):
                value=int(text)
                if not 0<=value<=1_000_000 or (field in ('goal','threshold') and value<1) or (field=='weekday' and value>6):raise ValueError('Valeur hors limites.')
            elif field in ('ads','vip_ads'):
                if m.get('photo'):
                    return self.show(uid,'Identifiant de ta photo :\n'+m['photo'][-1]['file_id']+'\n\nInsère-le dans le champ photo du JSON.',[[b('Annuler','abort')]],'admin')
                value=json.loads(text)
                if not isinstance(value,list) or len(value)>20:raise ValueError('Maximum 20 publicités.')
                for ad in value:
                    hours=ad.get('hours',4 if field=='ads' else 6)
                    if hours not in ((2,4,6) if field=='ads' else (0,6,12)) or not ad.get('text') or len(ad['text'])>1000:raise ValueError('Publicité invalide : texte et intervalle requis.')
            else:
                if not text or len(text)>2000:raise ValueError('Texte requis, maximum 2000 caractères.')
                value=text
            c[field]=value;db.run('UPDATE groups SET cfg=? WHERE id=?',(json.dumps(c),gid));screen='ag:'+gid
        else:
            s.require_admin(uid)
            if field=='grant_user':
                target,gid=text.split();target=int(target);s.group(gid)
                if s.banned(target):raise ValueError('Membre banni : accès refusé.')
                s.grant(target,gid,'admin')
            elif field=='unban_user':
                target=int(text);ban=s.banned(target)
                if ban and ban['permanent']:raise ValueError('Bannissement définitif non réversible ici.')
                db.run('DELETE FROM bans WHERE uid=?',(target,))
                for g in db.all('SELECT * FROM groups'):s.enqueue(f'unban:{g["id"]}:{target}','unban',{'gid':g['id'],'uid':target})
            elif field=='refund':
                o=db.one("SELECT * FROM orders WHERE id=? AND status='paid'",(text,))
                if not o:raise ValueError('Commande payée introuvable.')
                s.tg.call('refundStarPayment',user_id=o['uid'],telegram_payment_charge_id=o['charge'])
                db.run("UPDATE orders SET status='refunded' WHERE id=?",(text,))
                for item in json.loads(o['items']):
                    owner=db.one('SELECT * FROM access_orders WHERE gid=? AND uid=?',(item['gid'],o['uid']))
                    if owner and owner['order_id']==text:
                        db.run("DELETE FROM access WHERE gid=? AND uid=? AND source='stars'",(item['gid'],o['uid']))
                        s.enqueue('refund:'+text+':'+item['gid'],'kick',{'gid':item['gid'],'uid':o['uid']})
            elif field in ('rules','words','unban_goal','portal_text','support','terms'):
                value=text.splitlines() if field=='words' else int(text) if field=='unban_goal' else text
                if not value or len(text)>2500 or (field=='unban_goal' and not 1<=value<=10000):raise ValueError('Valeur invalide.')
                db.set(field,value)
            else:raise ValueError('Réglage inconnu.')
            screen='admin'
        db.run('UPDATE users SET wizard=NULL WHERE id=?',(uid,));s.audit(uid,'setting',wizard);return self.render(uid,screen)

    def publish(self,g):
        text=self.s.db.setting('portal_text','🌙 Bienvenue dans notre communauté\n\nDécouvre nos groupes. Invite tes amis pour débloquer tes accès ou choisis un coupe-file.\n\nDes VIP, un espace 24/24 et un groupe chat gratuit t’attendent !')
        rows=[[url('🚪 Découvrir les groupes',f'https://t.me/{self.username}?start=home')],[url('🎯 Mes invitations',f'https://t.me/{self.username}?start=invite'),url('👤 Mes accès',f'https://t.me/{self.username}?start=access')]]
        self.sch.post(g,'portal',text,rows)
