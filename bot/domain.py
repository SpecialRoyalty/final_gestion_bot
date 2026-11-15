import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROLES = {'portal':'Portail','chat':'Chat en rotation','always':'Le 24/24','vip':'VIP','free':'Groupe chat gratuit','proof':'Visite découverte'}
SLOTS = [(22,0),(22,1),(23,2),(15,18),(18,22)]
DEFAULTS = dict(description='',goal=5,price=0,monthly=False,locker=False,threshold=100,slot=1,weekday=2,rules='',dedup=False,inactivity=False,enabled=True,ads=[],vip_ads=[],reports=False)

def config(g):
    return DEFAULTS | json.loads(g['cfg'])

def now():
    return int(time.time())

def present(member):
    return member['status'] in ('creator','administrator','member') or (member['status']=='restricted' and member.get('is_member',False))

def window(day, slot, tz):
    a,b = SLOTS[slot]
    start = datetime.combine(day,datetime.min.time(),ZoneInfo(tz)).replace(hour=a)
    end = start.replace(hour=b)
    if b <= a:
        end += timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())

def countdown(seconds):
    if seconds >= 86400:
        return f'{(seconds+86399)//86400} jour(s)'
    if seconds >= 3600:
        return f'{(seconds+3599)//3600} h'
    mins=max(1,(seconds+59)//60)
    for bucket in (1,2,3,4,5,10,15,30,60):
        if mins<=bucket:return f'{bucket} min'
    return '1 h'

def progress(n,goal):
    goal=max(goal,1)
    filled=min(10,n*10//goal)
    return '🟩'*filled+'⬜'*(10-filled)+f' {min(n,goal)}/{goal}'

def active(access,t):
    return bool(access and (access['expires']==0 or access['expires']>t))

def eligible_order(groups,owned,banned=False):
    if banned:
        raise ValueError('Ton accès est suspendu.')
    if not groups:
        raise ValueError('Ton panier est vide.')
    for g in groups:
        c=config(g)
        if g['id'] in owned:
            raise ValueError('Un accès de ce panier est déjà actif. Actualise ton panier.')
        if g['role'] not in ('chat','always','vip') or not g['healthy'] or not c['enabled'] or c['price']<=0:
            raise ValueError('Un groupe n’est plus disponible à la vente.')
        if g['role']=='vip' and not c['description'].strip():
            raise ValueError('Description VIP manquante.')
    return [{'gid':g['id'],'name':g['name'],'price':config(g)['price'],'days':30 if config(g)['monthly'] else 0} for g in groups]
