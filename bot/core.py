from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re
import unicodedata

ROLES = ('portal', 'chat', 'always', 'vip', 'proof', 'free')
SLOTS = ('22-00', '22-01', '23-02', '15-18', '18-22')

def progress(value, target):
    target = max(1, target)
    value = max(0, value)
    ratio = min(value / target, 1)
    filled = int(ratio * 10)
    return '🟩' * filled + '⬜' * (10-filled) + f' {int(ratio*100)} %\n{value} / {target}'

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

def countdown(seconds):
    minutes = max(0, int(seconds // 60))
    if minutes >= 1440: return f'{minutes // 1440} j'
    if minutes >= 60: return f'{minutes // 60} h'
    for n in (30, 15, 10, 5, 4, 3, 2, 1):
        if minutes >= n: return f'{n} min'
    return 'moins d’une minute'
