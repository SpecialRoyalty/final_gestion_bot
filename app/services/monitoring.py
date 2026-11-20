"""Operational diagnostics and measured statistics; no inferred payment confirmations."""
import html
import json
import logging
import re
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from app.keyboards.inline import button
from app.services.payments import money
from app.services.telegram import transient_error, DeferredRequest
from app.services.recovery import GroupUnavailable


def safe_error(error):
    text=f'{type(error).__name__}: {error}'
    text=re.sub(r'https?://\S+','[URL masquée]',text)
    text=re.sub(r'\b\d{5,}:[A-Za-z0-9_-]+','[secret masqué]',text)
    return text[:350]


class Monitoring:
    def metric(self,kind,uid=None,gid=None,key=None):
        self.db.run('INSERT INTO metric_events(id,kind,uid,gid,created) VALUES (?,?,?,?,?) ON CONFLICT(id) DO NOTHING',
                    (key or uuid.uuid4().hex,kind,uid,gid,time.time()))

    def record_success(self,key):
        self.db.set(key+':error','')
        self.db.set(key+':failure_count',0)

    def record_failure(self,key,error):
        if isinstance(error,(DeferredRequest,GroupUnavailable)):return
        message=safe_error(error);now=time.time()
        last=float(self.db.setting(key+':error_at','0'))
        count=int(self.db.setting(key+':failure_count','0'))+1 if now-last<300 else 1
        self.db.set(key+':error',message)
        self.db.set(key+':error_at',now)
        self.db.set(key+':failure_count',count)
        # Keep every current failure visible in diagnostics; notify only persistent outages.
        if transient_error(error):
            logging.info('%s — incident temporaire (%s)',key,type(error).__name__)
            if count>=3:self.alert('Incident Telegram persistant : '+message+' Consulter l’état du réseau pour les groupes concernés.')
        else:
            logging.warning('%s — %s',key,message)
            self.alert(key+' : '+message)

    def time_label(self,stamp):
        return datetime.fromtimestamp(float(stamp),ZoneInfo(self.tz)).strftime('%d/%m %H:%M:%S') if stamp else 'jamais'

    def monitoring_callback(self,uid,data):
        if not (data=='health' or data in ('healthrun','healthmediaretry','healthportalretry','healthportalfailed','healthportaluncertain') or data.startswith(('health:','stats:'))): return False
        if uid not in self.owners: return True
        if data.startswith('stats:'):
            try: days=int(data.split(':')[1]); page=int(data.split(':')[2]) if data.count(':')>1 else 0
            except ValueError: return True
            if days in (1,7,30): self.show_stats(uid,days,max(page,0))
        elif data=='healthportalretry':
            self.screen(uid,'Relancer les notifications du dernier remplacement ? Une livraison non confirmée peut déjà avoir été reçue : sa relance peut produire un doublon.',[[button('Relancer uniquement les échecs confirmés','healthportalfailed')],[button('Relancer aussi les livraisons non confirmées','healthportaluncertain')],[button('⬅️ Retour','health')]])
        elif data in ('healthportalfailed','healthportaluncertain'):
            move=self.db.one('SELECT id FROM portal_moves ORDER BY created DESC LIMIT 1')
            if move:
                states="('failed','uncertain')" if data=='healthportaluncertain' else "('failed')"
                self.db.run("UPDATE portal_recipients SET state='ready',attempts=0,due=0 WHERE move_id=? AND state IN "+states,(move['id'],))
            self.show_health(uid)
        elif data=='healthmediaretry':
            from app.services.media import retry_media_failures
            retry_media_failures(self.db)
            self.show_health(uid)
        elif data=='healthrun':
            groups=self.db.all('SELECT id FROM groups WHERE enabled=1 ORDER BY id')
            if not self.db.setting('diagnostics:queue') or self.db.setting('diagnostics:queue')=='[]':
                self.db.set('diagnostics:queue',json.dumps([g['id'] for g in groups]))
                self.db.set('diagnostics:requested',time.time())
            self.show_health(uid)
        else:
            try: page=int(data.split(':')[1]) if ':' in data else 0
            except ValueError: return True
            self.show_health(uid,max(0,page))
        return True

    def task_diagnostics(self,now):
        queue=json.loads(self.db.setting('diagnostics:queue','[]'))
        if not queue: return
        gid=queue[0]; g=self.group(gid)
        if g:
            checks=[]
            try:
                self.check_rights(g)
                checks.append('Droits administrateur : OK')
                chat=self.api.call('getChat',chat_id=g['chat'])
                count=max(0,self.api.call('getChatMemberCount',chat_id=g['chat'])-1)
                self.db.set('member_count:'+gid,count)
                checks.append(f'Membres présents (hors bot) : {count}')
                if g['role'] in ('chat','free'):
                    permissions=chat.get('permissions')
                    if permissions is None: checks.append('Permissions d’envoi : non retournées par Telegram')
                    elif bool(permissions.get('can_send_messages',False)) != bool(g['opened']):
                        checks.append('⚠️ Permissions d’envoi incohérentes avec l’état ouvert/fermé')
                    else: checks.append('Permissions d’envoi : cohérentes')
                if g['role'] in ('chat','always','vip') and not g['description'].strip():
                    checks.append('⚠️ Description manquante')
                if g['role']=='portal' and not self.db.one("SELECT 1 FROM notices WHERE gid=? AND kind='portal'",(gid,)):
                    checks.append('⚠️ Aucun message de portail enregistré')
                if g['role'] in ('chat','free','always','vip','proof') and not g['rules'].strip():checks.append('⚠️ Règles du groupe manquantes')
                if g['role']=='vip' and not self.payment_options(gid): checks.append('⚠️ Aucun moyen de paiement configuré')
                if not any(check.startswith('⚠️') for check in checks): self.db.set('health:'+gid,time.time())

            except Exception as error:
                checks.append('⚠️ '+safe_error(error))
            self.db.set('diagnostics:'+gid,json.dumps({'at':time.time(),'checks':checks},ensure_ascii=False))
        self.db.set('diagnostics:queue',json.dumps(queue[1:]))

    def show_health(self,uid,page=0):
        now=time.time(); self.db.one('SELECT 1 AS ok')
        def pulse(key):
            stamp=float(self.db.setting(key,'0'))
            return ('🟢' if stamp and now-stamp<90 else '🔴')+' '+self.time_label(stamp)
        lines=['<b>🩺 État du réseau</b>',f'Fuseau : {html.escape(self.tz)}','Base de données : 🟢 accessible',
               'ℹ️ Les statistiques historiques couvrent uniquement les événements reçus par le bot.',
               'Réception Telegram : '+pulse('poll:ok'),
               'Dernier cycle terminé : '+pulse('scheduler:completed'),
               'Traitement médias : '+pulse('media:heartbeat')]
        pending=self.db.one("SELECT COUNT(*) AS n FROM update_inbox WHERE state='ready'")['n']
        failed_updates=self.db.one("SELECT COUNT(*) AS n FROM update_inbox WHERE state='failed'")['n']
        lines.append(f'Événements en attente : {pending} · échecs à examiner : {failed_updates}')
        failed=self.db.one('SELECT COUNT(*) AS n FROM failed_actions')['n']
        lines.append(f'Actions arrêtées, à examiner : {failed}')
        labels={'panel_cleanup':'Anciens panneaux non supprimables','media:size':'Médias trop volumineux','media:format':'Médias non décodables','media:network':'Médias arrêtés après incidents réseau','media:timeout':'Médias trop longs à décoder','media:error':'Erreurs médias à examiner','media':'Anciens échecs médias (cause détaillée non enregistrée)'}
        for item in self.db.all('SELECT kind,COUNT(*) AS n FROM failed_actions GROUP BY kind ORDER BY kind LIMIT 12'):
            lines.append('• '+html.escape(labels.get(item['kind'],item['kind']))+' : '+str(item['n']))
        lines.append('Ces compteurs incluent l’historique ; ils ne signifient pas que les tâches tournent encore.')
        for item in self.db.all('SELECT category,detail FROM media_failures ORDER BY created DESC LIMIT 2'):
            lines.append('⚠️ Média · '+html.escape(item['detail']))
        for item in self.db.all('SELECT p.reason,g.title FROM group_pauses p JOIN groups g ON g.chat=p.chat'):
            lines.append('⏸ '+html.escape(item['title']+' : '+item['reason']))
        move=self.db.one('SELECT id FROM portal_moves ORDER BY created DESC LIMIT 1')
        if move:
            labels={'ready':'en attente','sent':'envoyées','unreachable':'non joignables','uncertain':'résultat non confirmé','failed':'en échec','sending':'en cours','superseded':'remplacées'}
            counts=self.db.all('SELECT state,COUNT(*) AS n FROM portal_recipients WHERE move_id=? GROUP BY state',(move['id'],))
            lines.append('Notifications du portail : '+', '.join(str(r['n'])+' '+labels.get(r['state'],r['state']) for r in counts))
        lines.append('Accusés boutons en échec (sans bloquer l’action) : '+self.db.setting('callback_ack:errors','0'))
        for lane in ('private:0','private:1','private:2','private:3','service','admission:0','admission:1','urgent:0','urgent:1'):
            duration=self.db.setting('latency:'+lane+':ms')
            if duration:lines.append(lane+' : dernier traitement '+duration+' ms · attente avant traitement '+self.db.setting('latency:'+lane+':queue_ms','0')+' ms')
        incidents=self.db.all('SELECT key,value FROM settings WHERE key LIKE ? AND value!=? ORDER BY key',('%:error',''))
        recent=[]
        for incident in incidents:
            stamp=float(self.db.setting(incident['key']+'_at','0'))
            if now-stamp<86400:recent.append((stamp,incident))
        for stamp,incident in sorted(recent,key=lambda item:item[0],reverse=True)[:5]:
            lines.append('⚠️ '+html.escape(incident['key'][:-6])+' · '+self.time_label(stamp)+' : '+html.escape(incident['value']))
        config=self.configuration_summary()
        lines.insert(1,f'Configuration : {config["percent"]} % ({config["done"]}/{config["total"]} critères)')
        lines.insert(2,'Ce pourcentage ne mesure pas le fonctionnement technique.')
        lines.append('Dernier /start reçu : '+self.time_label(self.db.setting('start:received')))
        lines.append('Dernier /start affiché : '+self.time_label(self.db.setting('start:rendered')))
        start_error=self.db.setting('start:error')
        if start_error: lines.append('⚠️ /start : '+html.escape(start_error))
        main_error=self.db.setting('main:error')
        if main_error and float(self.db.setting('main:error_at','0'))>float(self.db.setting('poll:ok','0')):
            lines.append('⚠️ Réception : '+html.escape(main_error))
        failed=self.db.setting('scheduler:failed')
        if failed: lines.append('⚠️ Tâches en erreur : '+html.escape(failed))
        queue=json.loads(self.db.setting('diagnostics:queue','[]'))
        if queue: lines.append(f'Vérifications Telegram en cours : {len(queue)} groupes restants.')
        for label,table in (('Actions', 'jobs'),('Médias','media_tasks')):
            count=self.db.one('SELECT COUNT(*) AS n FROM '+table)['n']
            errors=self.db.one('SELECT COUNT(*) AS n FROM '+table+' WHERE attempts>0')['n']
            lines.append(f'{label} en attente : {count} · en reprise : {errors}')
        expired=self.db.one('SELECT COUNT(*) AS n FROM access WHERE revoked=0 AND expires IS NOT NULL AND expires<=?',(now,))['n']
        if expired: lines.append(f'⚠️ {expired} accès expirés restent à révoquer.')
        opens=self.db.one("SELECT COUNT(*) AS n FROM groups WHERE role='chat' AND opened=1")['n']
        if opens>1: lines.append('🔴 Plusieurs groupes classiques sont marqués ouverts.')
        groups=self.db.all('SELECT * FROM groups ORDER BY title')
        pages=max(1,(len(groups)+1)//2); page=min(page,pages-1)
        for g in groups[page*2:page*2+2]:
            gid=g['id']; lines.append('\n<b>'+html.escape(g['title'])+'</b> · '+('actif' if g['enabled'] else 'désactivé'))
            missing=[c['label'] for c in self.configuration_checks(g) if not c['ok']]
            if missing: lines.append('⚠️ À configurer : '+html.escape(', '.join(missing)))
            lines.append('Contrôle du planning : '+self.time_label(self.db.setting('group:'+gid+':ok')))
            lines.append('Annonce modifiée : '+self.time_label(self.db.setting('notice:'+gid+':status:changed')))
            error=self.db.setting('group:'+gid+':error') or self.db.setting('announce:'+gid+':error') or self.db.setting('portal_error:'+gid)
            if error: lines.append('⚠️ '+html.escape(error))
            report=json.loads(self.db.setting('diagnostics:'+gid,'{}'))
            if report:
                lines.append('Contrôles Telegram du '+self.time_label(report['at']))
                lines.extend(html.escape(x) for x in report['checks'] if x!='Historique des membres incomplet si le bot était absent.')
            else: lines.append('Contrôles Telegram détaillés : pas encore exécutés.')
        rows=[[button('📢 Notifications du portail : relancer','healthportalretry')],[button('🔁 Réessayer les médias temporaires (20 max)','healthmediaretry')],[button('⚙️ Compléter la configuration','configuration')],[button('🔎 Vérifier les groupes','healthrun')],[button('🔄 Actualiser','health:'+str(page))]]
        if pages>1: rows.append([button('◀️','health:'+str(max(0,page-1))),button(f'{page+1}/{pages} ▶️','health:'+str(min(pages-1,page+1)))])
        rows.append([button('⬅️ Administration','admin')])
        visible=[]
        for line in lines:
            if sum(len(x)+1 for x in visible)+len(line)>3750:
                visible.append('… Affichage abrégé. Utilise les pages des groupes.');break
            visible.append(line)
        self.screen(uid,'\n'.join(visible),rows)

    def show_stats(self,uid,days=7,page=0):
        now=time.time(); since=now-days*86400
        count=lambda sql,args=(): self.db.one(sql,args)['n']
        start=self.db.setting('metrics:since')
        lines=[f'<b>📊 Statistiques · {days} jour(s)</b>',
               'Événements mesurés depuis : '+self.time_label(start),
               'Les données antérieures non enregistrées ne sont pas reconstituées.',
               f'Utilisateurs connus : {count("SELECT COUNT(*) AS n FROM users")}',
               f'Invitations validées (total) : {count("SELECT COUNT(*) AS n FROM referrals WHERE valid=1")}',
               f'Invitations en attente : {count("SELECT COUNT(*) AS n FROM referrals WHERE valid=0 AND present=1")}',
               f'Accès actifs maintenant : {count("SELECT COUNT(*) AS n FROM access a LEFT JOIN users u ON u.id=a.uid WHERE a.revoked=0 AND (a.expires IS NULL OR a.expires>?) AND COALESCE(u.banned,0)=0 AND COALESCE(u.permanent,0)=0",(now,))}']
        lines.append('Signalements en attente : '+str(count("SELECT COUNT(*) AS n FROM bug_tickets WHERE status='pending'")))
        lines.append('Crédits de bugs attribués (distincts des invitations) : '+str(5*count("SELECT COUNT(*) AS n FROM bug_tickets WHERE status='approved'")))
        validated=count("SELECT COUNT(*) AS n FROM metric_events WHERE kind='referral_valid' AND created>=?",(since,))
        lines.append(f'Invitations validées enregistrées sur la période : {validated}')
        pending=count("SELECT COUNT(*) AS n FROM manual_orders WHERE status='review'")
        late=count("SELECT COUNT(*) AS n FROM manual_orders WHERE status='review' AND created<?",(now-86400,))
        lines.append(f'Paiements à vérifier : {pending} · plus de 24 h : {late}')
        for row in self.db.all("SELECT currency,SUM(total) AS amount,COUNT(*) AS n FROM manual_orders WHERE status='approved' AND reviewed_at>=? GROUP BY currency",(since,)):
            lines.append(f'Validés par admin : {row["n"]} paiements · '+money(row['amount'],row['currency']))
        groups=self.db.all("SELECT * FROM groups WHERE role!='unset' ORDER BY title")
        pages=max(1,(len(groups)+2)//3);page=min(page,pages-1)
        for g in groups[page*3:page*3+3]:
            gid=g['id']
            visitors=count("SELECT COUNT(DISTINCT uid) AS n FROM metric_events WHERE gid=? AND kind='view' AND created>=?",(gid,since))
            joins=count("SELECT COUNT(*) AS n FROM metric_events WHERE gid=? AND kind='join' AND created>=?",(gid,since))
            leaves=count("SELECT COUNT(*) AS n FROM metric_events WHERE gid=? AND kind='leave' AND created>=?",(gid,since))
            active=count('SELECT COUNT(*) AS n FROM members WHERE gid=? AND present=1',(gid,))
            grants=count("SELECT COUNT(DISTINCT uid) AS n FROM metric_events WHERE gid=? AND kind='grant' AND created>=?",(gid,since))
            lines += ['\n<b>'+html.escape(g['title'])+'</b>',f'Visiteurs de la fiche : {visitors} · personnes ayant reçu un accès : {grants}',
                      f'Entrées/sorties observées : +{joins} / −{leaves} · net {joins-leaves:+d}',
                      f'Membres suivis présents : {active}']
            advice=[]
            if not g['description'].strip() and g['role'] in ('chat','always','vip'): advice.append('Compléter la description avant de promouvoir ce groupe.')
            if visitors>=5 and grants==0: advice.append('Des personnes consultent la fiche sans accès accordé sur la période : examiner la description et l’objectif.')
            if leaves>joins and leaves>=3: advice.append('Plus de sorties que d’entrées observées : examiner la participation et les horaires.')
            if g['locker'] and not g['opened']: advice.append('Locker actif : consulter les contrôles pour comparer le seuil au nombre réel de membres.')
            lines.extend('💡 '+a for a in advice)
        if late: lines.append('\n💡 Traiter les paiements en attente de plus de 24 h avant d’augmenter la promotion.')
        rows=[[button('24 h','stats:1'),button('7 jours','stats:7'),button('30 jours','stats:30')]]
        if pages>1: rows.append([button('◀️',f'stats:{days}:{max(0,page-1)}'),button(f'{page+1}/{pages} ▶️',f'stats:{days}:{min(pages-1,page+1)}')])
        rows += [[button('🔄 Actualiser',f'stats:{days}:{page}'),button('⬅️ Administration','admin')]]
        self.screen(uid,'\n'.join(lines),rows)
