"""Targeted referrals, launch acknowledgements and reviewed bug rewards."""
import hashlib
import html
import time
import uuid
from urllib.parse import urlencode
from app.keyboards.inline import button, keyboard
from app.services.payments import METHODS, money, total
from app.services.invite_links import PERMANENT_LINK

class MemberWorkflows:
    def initialize_member_workflows(self):
        if self.db.setting('workflows:v10'): return
        with self.db.transaction():
            # Preserve previously earned access before switching counters to group targets.
            self.db.run("INSERT INTO access(uid,gid,source) SELECT r.inviter,g.id,'referral' FROM groups g CROSS JOIN (SELECT inviter,COUNT(*) AS n FROM referrals WHERE valid=1 GROUP BY inviter) r LEFT JOIN access a ON a.uid=r.inviter AND a.gid=g.id WHERE g.enabled=1 AND g.role IN ('chat','always') AND a.uid IS NULL AND r.n>=g.goal ON CONFLICT DO NOTHING")
            self.db.run('UPDATE access SET base=0')
            # Existing enabled groups are not presented as new on deployment.
            self.db.run("INSERT INTO group_launches(gid,created) SELECT id,0 FROM groups WHERE enabled=1 AND role!='portal' ON CONFLICT DO NOTHING")
            for link in self.db.all("SELECT l.* FROM links l WHERE l.purpose='referral' AND l.expires!=0 AND NOT EXISTS (SELECT 1 FROM target_links t WHERE t.url=l.url)"):
                portal=self.group(link['gid'])
                if portal:self.enqueue('revokeChatInviteLink',dict(chat_id=portal['chat'],invite_link=link['url']))
                self.db.run('UPDATE links SET expires=0 WHERE url=?',(link['url'],))
            self.db.set('workflows:v10',time.time())

    def launch_gate(self,uid):
        if uid in self.owners or self.user(uid)['permanent']: return False
        row=self.db.one("SELECT g.* FROM group_launches n JOIN groups g ON g.id=n.gid WHERE n.created>0 AND g.enabled=1 AND g.role!='portal' AND NOT EXISTS (SELECT 1 FROM launch_reads r WHERE r.uid=? AND r.gid=n.gid) ORDER BY n.created,n.gid LIMIT 1",(uid,))
        if not row: return False
        text=self.tr(uid,'🆕 Nouveau groupe disponible')+'\n\n<b>'+html.escape(row['title'])+'</b>\n'
        description=self.db.setting('description:'+row['id']+':'+self.language(uid),row['description'])
        text+=html.escape(description)+'\n\n'
        if row['role'] in ('chat','always'):
            text+=str(row['goal'])+' '+self.tr(uid,'invitations pour débloquer ce groupe.')+'\n'
        elif row['role']=='free': text+=self.tr(uid,'Accès gratuit selon le calendrier du groupe.')+'\n'
        elif row['role']=='proof': text+=self.tr(uid,'Une visite de 10 minutes, une seule fois.')+'\n'
        options=self.payment_options(row['id'])
        for price in options:
            name,currency,fee=METHODS[price['method']]
            text+=name+' : '+money(total(price['cents'],fee),currency)+(self.tr(uid,' / 30 jours') if row['role']=='vip' and row['monthly'] else '')+'\n'
        if row['role']=='vip' and not options: text+=self.tr(uid,'Paiement momentanément indisponible.')+'\n'
        self.db.set('launch_shown:'+str(uid),row['id'])
        self.screen(uid,text,[[button(self.tr(uid,'✅ J’ai compris'),'launchack:'+row['id'])]])
        return True

    def acknowledge_launch(self,uid,gid):
        if self.db.setting('launch_shown:'+str(uid))==gid and self.db.one('SELECT gid FROM group_launches WHERE gid=? AND created>0',(gid,)):
            self.db.run('INSERT INTO launch_reads(uid,gid) VALUES (?,?) ON CONFLICT DO NOTHING',(uid,gid))
        self.db.run('UPDATE users SET pending=NULL WHERE id=?',(uid,))
        return self.show(uid,'member')

    def invitation_screen(self,uid,gid=None):
        self.allocate_reserve(uid)
        reserve=self.db.one('SELECT COUNT(*) AS n FROM referral_reserves WHERE uid=?',(uid,))['n']
        reserve_text='\n\n'+self.tr(uid,'Invitations en réserve : ')+str(reserve)
        groups=self.db.all("SELECT * FROM groups WHERE enabled=1 AND role IN ('chat','always') ORDER BY title")
        if not gid:
            rows=[[button(g['title'],'invite:'+g['id'])] for g in groups]
            return self.screen(uid,(self.tr(uid,'Choisis ton objectif prioritaire. Chaque lien mène au portail. Les invitations en surplus passent aux autres objectifs.') if groups else self.tr(uid,'Aucun objectif d’invitation disponible.'))+reserve_text,rows+[[button(self.tr(uid,'⬅️ Accueil'),'home')]])
        g=next((g for g in groups if g['id']==gid),None)
        if not g: return self.invitation_screen(uid)
        portal=self.db.one("SELECT * FROM groups WHERE enabled=1 AND role='portal'")
        if not portal: return self.screen(uid,self.tr(uid,'Le portail sera bientôt disponible.'),[[button(self.tr(uid,'⬅️ Accueil'),'home')]])
        url=self.target_link(uid,g,portal)
        share='https://t.me/share/url?'+urlencode({'url':url,'text':self.tr(uid,'Rejoins notre portail !')})
        text='<b>'+html.escape(g['title'])+'</b>\n\n'+self.tr(uid,'Ce lien mène au portail et remplit ce groupe en priorité. Une fois complet, les invitations passent à l’objectif auquel il en manque le moins. Si tout est complet, elles sont conservées en réserve.')+'\n\n'+html.escape(url)+reserve_text
        self.screen(uid,text,[[button(self.tr(uid,'📤 Partager mon invitation'),url=share)],
            [button(self.tr(uid,'🔄 Régénérer mon lien'),'regen:'+gid)],
            [button(self.tr(uid,'⬅️ Retour'),'g:'+gid),button(self.tr(uid,'Changer d’objectif'),'invite')]])

    def target_link(self,uid,g,portal,regenerate=False):
        old=self.db.all("SELECT l.* FROM links l JOIN target_links t ON t.url=l.url WHERE l.uid=? AND t.gid=? AND l.gid=? AND l.purpose='referral' AND (l.expires=-1 OR l.expires>?) ORDER BY CASE WHEN l.expires=-1 THEN 0 ELSE 1 END,l.expires DESC",(uid,g['id'],portal['id'],time.time()))
        if old and not regenerate:return self.make_link_permanent(old[0],portal)
        expires=PERMANENT_LINK
        result=self.api.call('createChatInviteLink',chat_id=portal['chat'],name=f'ref:{uid}:{g["id"]}'[:32])
        url=result['invite_link']
        try:
            if regenerate:
                for link in old:
                    self.api.call('revokeChatInviteLink',chat_id=portal['chat'],invite_link=link['url'])
                    self.db.run('UPDATE links SET expires=0 WHERE url=?',(link['url'],))
            with self.db.transaction():
                self.db.run("INSERT INTO links(url,gid,uid,purpose,expires) VALUES (?,?,?,'referral',?)",(url,portal['id'],uid,expires))
                self.db.run('INSERT INTO target_links(url,gid) VALUES (?,?)',(url,g['id']))
        except Exception:
            self.enqueue('revokeChatInviteLink',dict(chat_id=portal['chat'],invite_link=url))
            raise
        return url

    def words_screen(self,uid,page=0):
        if uid not in self.owners: return
        words=self.db.setting('words').splitlines();page=max(0,min(page,max(0,(len(words)-1)//12)))
        rows=[]
        for word in words[page*12:page*12+12]:
            ident=hashlib.sha256(word.encode()).hexdigest()[:20]
            rows.append([button('🗑 '+word[:45],'wordremove:'+ident)])
        nav=[]
        if page: nav.append(button('⬅️ Précédent','words:'+str(page-1)))
        if (page+1)*12<len(words): nav.append(button('Suivant ➡️','words:'+str(page+1)))
        if nav:rows.append(nav)
        rows += [[button('➕ Ajouter','wordadd')],[button('⬅️ Administration','admin')]]
        self.screen(uid,'<b>🚫 Mots interdits</b>\n'+str(len(words))+' terme(s). Sélectionne un terme pour confirmer sa suppression.',rows)

    def workflow_callback(self,uid,data):
        parts=data.split(':');action=parts[0]
        if action=='resetfastpass' and uid in self.owners:
            self.db.run("DELETE FROM settings WHERE key='fastpass_description'")
            self.show(uid,'admin')
        elif action=='regen' and len(parts)==2:
            self.screen(uid,self.tr(uid,'Remplacer ce lien ? L’ancien sera désactivé. Les invitations déjà acquises sont conservées.'),[[button(self.tr(uid,'Confirmer'),'regenconfirm:'+parts[1])],[button(self.tr(uid,'⬅️ Retour'),'invite:'+parts[1])]])
        elif action=='regenconfirm' and len(parts)==2:
            g=self.group(parts[1]);portal=self.db.one("SELECT * FROM groups WHERE role='portal' AND enabled=1")
            if g and g['enabled'] and g['role'] in ('chat','always') and portal:self.target_link(uid,g,portal,True)
            self.invitation_screen(uid,parts[1])
        elif action=='bug':
            groups=self.db.all("SELECT * FROM groups WHERE enabled=1 AND role IN ('chat','always') ORDER BY title")
            self.screen(uid,self.tr(uid,'Décris un problème réel. Après validation admin, tu recevras +5 invitations pour le groupe choisi. Les doublons ne sont pas récompensés.'),[[button(g['title'],'bugtarget:'+g['id'])] for g in groups]+[[button(self.tr(uid,'Mes signalements'),'mybugs')],[button(self.tr(uid,'⬅️ Accueil'),'home')]])
        elif action=='bugtarget' and len(parts)==2:
            g=self.group(parts[1])
            if not g or not g['enabled'] or g['role'] not in ('chat','always'): return True
            self.db.run('UPDATE users SET pending=? WHERE id=?',(data,uid))
            self.screen(uid,self.tr(uid,'Explique le problème, les étapes pour le reproduire et le résultat attendu (20 à 2000 caractères).'),[[button(self.tr(uid,'⬅️ Retour'),'bug')]])
        elif action=='mybugs':
            rows=self.db.all('SELECT * FROM bug_tickets WHERE uid=? ORDER BY created DESC LIMIT 20',(uid,))
            text=self.tr(uid,'Mes signalements')+'\n\n'+'\n'.join(html.escape(r['body'][:70])+' — '+self.tr(uid,{'pending':'En attente','approved':'Validé · +5','rejected':'Refusé'}[r['status']]) for r in rows)
            self.screen(uid,text,[[button(self.tr(uid,'⬅️ Retour'),'bug')]])
        elif action in ('bugs','bugreview','bugapprove','bugreject'):
            if uid not in self.owners:return True
            if action=='bugs':
                rows=self.db.all("SELECT * FROM bug_tickets WHERE status='pending' ORDER BY created LIMIT 40")
                self.screen(uid,'<b>🐞 Signalements à vérifier</b>',[[button(str(r['uid'])+' · '+r['body'][:40],'bugreview:'+r['id'])] for r in rows]+[[button('⬅️ Administration','admin')]])
            elif len(parts)==2:
                if action in ('bugapprove','bugreject'):self.decide_bug(uid,parts[1],action=='bugapprove')
                r=self.db.one('SELECT * FROM bug_tickets WHERE id=?',(parts[1],))
                if r:
                    g=self.group(r['gid'])
                    rows=[[button('✅ Bug réel · attribuer +5','bugapprove:'+r['id']),button('❌ Refuser','bugreject:'+r['id'])]] if r['status']=='pending' else []
                    self.screen(uid,'<b>🐞 Signalement</b>\nUtilisateur : '+str(r['uid'])+'\nObjectif : '+html.escape(g['title'] if g else r['gid'])+'\nÉtat : '+r['status']+'\n\n'+html.escape(r['body']),rows+[[button('⬅️ Signalements','bugs')]])
        elif action in ('words','wordadd','wordremove','worddelete'):
            if uid not in self.owners:return True
            if action=='words':self.words_screen(uid,int(parts[1]) if len(parts)==2 and parts[1].isdigit() else 0)
            elif action=='wordadd':
                self.db.run("UPDATE users SET pending='wordadd' WHERE id=?",(uid,))
                self.screen(uid,'Envoie les nouveaux termes, un par ligne (maximum 100 caractères par terme).',[[button('⬅️ Annuler','words')]])
            elif len(parts)==2:
                words=self.db.setting('words').splitlines()
                found=next((w for w in words if hashlib.sha256(w.encode()).hexdigest()[:20]==parts[1]),None)
                if action=='wordremove' and found:
                    self.screen(uid,'Supprimer « '+html.escape(found)+' » ?',[[button('🗑 Confirmer','worddelete:'+parts[1]),button('⬅️ Annuler','words')]])
                else:
                    if found:self.db.set('words','\n'.join(w for w in words if w!=found))
                    self.words_screen(uid)
        else:return False
        return True

    def workflow_input(self,uid,m,pending):
        if pending=='wordadd':
            if uid not in self.owners:return True
            items=[w.strip() for w in m.get('text','').splitlines() if w.strip()]
            if not items or any(len(w)>100 for w in items):raise ValueError('Ajoute au moins un terme, maximum 100 caractères par terme.')
            existing=self.db.setting('words').splitlines()
            for word in items:
                if word.casefold() not in {w.casefold() for w in existing}:existing.append(word)
            if len(existing)>1000:raise ValueError('Maximum 1000 termes.')
            self.db.set('words','\n'.join(existing));self.db.run('UPDATE users SET pending=NULL WHERE id=?',(uid,));self.words_screen(uid);return True
        if not pending.startswith('bugtarget:'):return False
        gid=pending.split(':',1)[1];g=self.group(gid)
        if not g or g['role'] not in ('chat','always'):return True
        body=m.get('text','').strip()
        if not 20<=len(body)<=2000:raise ValueError(self.tr(uid,'Le signalement doit contenir entre 20 et 2000 caractères.'))
        if self.db.one('SELECT id FROM bug_tickets WHERE uid=? AND body=?',(uid,body)):
            raise ValueError(self.tr(uid,'Ce signalement existe déjà. Consulte Mes signalements.'))
        if self.db.one('SELECT COUNT(*) AS n FROM bug_tickets WHERE uid=? AND created>?',(uid,time.time()-86400))['n']>=3:
            raise ValueError(self.tr(uid,'Maximum 3 signalements par jour.'))
        oid=uuid.uuid4().hex
        with self.db.transaction():
            self.db.run('INSERT INTO bug_tickets(id,uid,gid,body,created) VALUES (?,?,?,?,?)',(oid,uid,gid,body,time.time()))
            self.db.run('UPDATE users SET pending=NULL WHERE id=?',(uid,))
            for owner in self.owners:self.enqueue('sendMessage',dict(chat_id=owner,text='🐞 Nouveau signalement de '+str(uid),reply_markup=keyboard([button('Examiner','bugreview:'+oid)])))
        self.refresh_panel(uid,lambda:self.workflow_callback(uid,'mybugs'))
        return True

    def decide_bug(self,uid,oid,approve):
        if uid not in self.owners:return
        with self.db.transaction():
            r=self.db.one('SELECT * FROM bug_tickets WHERE id=?'+('' if self.db.sqlite else ' FOR UPDATE'),(oid,))
            if not r or r['status']!='pending':return
            status='approved' if approve else 'rejected'
            self.db.run('UPDATE bug_tickets SET status=?,reviewed_by=?,reviewed_at=? WHERE id=?',(status,uid,time.time(),oid))
            self.db.run('INSERT INTO audit(id,actor,action,created) VALUES (?,?,?,?)',(uuid.uuid4().hex,uid,'bug:'+oid+':'+status,time.time()))
            self.enqueue('sendMessage',dict(chat_id=r['uid'],text=self.tr(r['uid'],'✅ Bug validé : +5 invitations ajoutés à ton objectif.' if approve else 'Ton signalement a été examiné et refusé.'),reply_markup=keyboard([button(self.tr(r['uid'],'Mes signalements'),'mybugs')])))
