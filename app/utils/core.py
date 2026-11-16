from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import math
import re
import unicodedata

ROLES = ('portal', 'chat', 'always', 'vip', 'proof', 'free')
SLOTS = ('22-00', '22-01', '23-02', '15-18', '18-22')

def progress(value, target):
    target = max(1, target)
    value = max(0, value)
    ratio = min(value / target, 1)
    filled = int(ratio * 12)
    return '▰' * filled + '▱' * (12-filled) + f'  {int(ratio*100)} %\n{value} / {target}'

def member_present(member):
    return member['status'] in ('member', 'administrator', 'creator') or (
        member['status'] == 'restricted' and member.get('is_member', False))

def active_access(row, now):
    return bool(row and not row['revoked'] and (row['expires'] is None or row['expires'] > now))

def window(day, slot, tz):
    start, end = map(int, slot.split('-'))
    zone = ZoneInfo(tz)
    a = datetime(day.year, day.month, day.day, start, tzinfo=zone)
    b = datetime(day.year, day.month, day.day, end, tzinfo=zone)
    if end <= start:
        b += timedelta(days=1)
    return a.timestamp(), b.timestamp()

def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text.casefold())
                   if not unicodedata.combining(c))

def forbidden(text, terms):
    text = normalized(text)
    return any(re.search(r'(?<!\w)' + re.escape(normalized(t)) + r'(?!\w)', text)
               for t in terms if t.strip())

def countdown(seconds, closing=False, weekly=False):
    """Stable announcement buckets: never a minute-by-minute distant countdown."""
    minutes=max(1,math.ceil(seconds/60))
    if weekly and minutes>1440: return f'environ {math.ceil(minutes/1440)} j'
    if minutes>60: return f'environ {math.ceil(minutes/60)} h'
    milestones=(60,30,15,5,4,3,2,1) if closing else ((60,30,15,10,5,4,3,2,1) if weekly else (60,30,10,5,4,3,2,1))
    bucket=next(value for value in reversed(milestones) if value>=minutes)
    return 'moins d’une heure' if bucket==60 else f'moins de {bucket} min'
