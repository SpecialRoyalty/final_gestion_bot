"""Configuration completeness: an explicit checklist, not an uptime or quality score."""
import html
from app.keyboards.inline import button
from app.services.payments import METHODS
from app.utils.core import SLOTS


class Readiness:
    def configuration_checks(self,g):
        gid=g['id'];role=g['role'];checks=[]
        def add(label,ok,target): checks.append({'label':label,'ok':bool(ok),'target':target})
        add('Rôle défini',role in ('portal','chat','always','vip','proof','free'),'roles:'+gid)
        add('Groupe activé',g['enabled'],'a:'+gid)
        if role=='unset': return checks
        add('Texte du portail' if role=='portal' else 'Description',g['description'].strip(),'edit:'+gid+':description')
        if role=='portal':
            add('Image du portail',self.db.setting('portal_photo:'+gid),'portalphoto:'+gid)
        if role in ('chat','always'):
            add('Objectif d’invitations',g['goal']>0,'edit:'+gid+':goal')
        if role in ('chat','free'):
            add('Horaires',g['slot'] in SLOTS,'slots:'+gid)
        if role in ('chat','free','always','vip','proof'):add('Règles du groupe',g['rules'].strip(),'edit:'+gid+':rules')
        if role=='free': add('Jour d’ouverture',0<=g['weekday']<=6,'edit:'+gid+':weekday')
        if g['locker'] and role not in ('portal','proof'):
            add('Seuil du locker',g['threshold']>0,'edit:'+gid+':threshold')
        if role in ('chat','always','vip'):
            prices={p['method']:p['cents'] for p in self.db.all('SELECT method,cents FROM payment_prices WHERE gid=?',(gid,))}
            for method,(name,currency,fee) in METHODS.items():
                add(name+' : coordonnées',self.db.setting('pay:'+method).strip(),'paydest:'+method)
                add(name+' : tarif du groupe',prices.get(method,0)>0,'price:'+gid+':'+method)
        return checks

    def configuration_summary(self):
        groups=self.db.all('SELECT * FROM groups ORDER BY title')
        checks=[{'label':'Portail actif','ok':any(g['role']=='portal' and g['enabled'] for g in groups)},
                {'label':'Au moins un groupe à rejoindre','ok':any(g['enabled'] and g['role'] not in ('portal','unset') for g in groups)}]
        for group in groups: checks.extend(self.configuration_checks(group))
        done=sum(c['ok'] for c in checks)
        return {'done':done,'total':len(checks),'percent':done*100//len(checks),'groups':groups}

    def readiness_callback(self,uid,data):
        if data!='configuration' and not data.startswith('configuration:'): return False
        if uid not in self.owners: return True
        try: page=max(0,int(data.split(':')[1])) if ':' in data else 0
        except ValueError: return True
        self.show_configuration(uid,page); return True

    def show_configuration(self,uid,page=0):
        summary=self.configuration_summary(); groups=summary['groups']
        pages=max(1,len(groups));page=min(page,pages-1)
        text=f'<b>⚙️ Configuration complète : {summary["percent"]} %</b>\n{summary["done"]} / {summary["total"]} critères renseignés.\n\nCe score décrit la configuration, pas le bon fonctionnement technique. Les trois moyens de paiement demandés sont inclus dans les critères des groupes payants.\n'
        rows=[]
        if not any(g['enabled'] and g['role']=='portal' for g in groups):text+='\n⚠️ Aucun portail actif.'
        if not any(g['enabled'] and g['role'] not in ('portal','unset') for g in groups):text+='\n⚠️ Aucun groupe actif à rejoindre.'
        if groups:
            g=groups[page];checks=self.configuration_checks(g);complete=sum(c['ok'] for c in checks)
            text+=f'\n\n<b>{html.escape(g["title"])}</b> · {complete}/{len(checks)}\n'
            text+='\n'.join(('✅ ' if c['ok'] else '⚠️ ')+html.escape(c['label']) for c in checks)
            if g['role'] in ('chat','always','vip'):
                available=self.payment_options(g['id'])
                text+='\n\n'+('💳 Paiement disponible : '+', '.join(METHODS[p['method']][0] for p in available) if available else '⛔ Aucun moyen de paiement utilisable : le bouton d’achat est masqué pour les membres.')
            seen=set()
            for check in checks:
                if not check['ok'] and check['target'] not in seen:
                    rows.append([button('✏️ '+check['label'],check['target'])]);seen.add(check['target'])
        if pages>1:rows.append([button('◀️','configuration:'+str(max(0,page-1))),button(f'{page+1}/{pages} ▶️','configuration:'+str(min(pages-1,page+1)))])
        rows += [[button('🔄 Actualiser','configuration:'+str(page)),button('🩺 État technique','health')],[button('⬅️ Administration','admin')]]
        self.screen(uid,text,rows)
