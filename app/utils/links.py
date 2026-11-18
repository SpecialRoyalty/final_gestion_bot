"""Detect clickable URL entities and common plain links, including captions."""
import re
import unicodedata

URL=re.compile(r'(?i)(?:https?://|tg://|www\.|(?:t|telegram)\.me/|(?<![\w@])(?:[\w-]+\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]+)(?=[/:?#\s]|$))')

def contains_link(message):
    for row in message.get('reply_markup',{}).get('inline_keyboard',[]):
        if any(b.get('url') or b.get('web_app',{}).get('url') or b.get('login_url',{}).get('url') for b in row):return True
    entities=message.get('entities',[])+message.get('caption_entities',[])
    if any(e.get('type') in ('url','text_link') for e in entities): return True
    text=unicodedata.normalize('NFKC',message.get('text',message.get('caption','')))
    text=''.join(c for c in text if unicodedata.category(c)!='Cf')
    text=re.sub(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}', '', text)
    return bool(URL.search(text))
