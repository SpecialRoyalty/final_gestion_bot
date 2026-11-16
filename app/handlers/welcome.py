"""Member language selection and owner-managed welcome content."""
import html
from app.keyboards.inline import button
from app.utils.i18n import LANGUAGES

class Welcome:
    def welcome_callback(self,uid,data):
        parts=data.split(':'); action=parts[0]
        if action=='language' and len(parts)==2:
            if parts[1] in LANGUAGES: self.db.set('language:'+str(uid),parts[1])
            self.show(uid,'member'); return True
        if action not in ('welcomeconfig','welcomelang','welcometext','welcomephoto','welcomeclear','localdesc','localfastpass'): return False
        if uid not in self.owners: return True
        if action=='welcomeconfig':
            self.screen(uid,'<b>🖼 Accueil et traductions</b>\nLe texte et les descriptions sont configurables par langue. Les traductions du bot sont intégrées. Les textes personnalisés ne sont pas traduits automatiquement.\n\nImage commune : '+('configurée' if self.db.setting('welcome_photo') else 'aucune'),
                [[button(name,'welcomelang:'+code)] for code,name in LANGUAGES.items()]+
                [[button('🖼 Modifier l’image','welcomephoto'),button('🗑 Retirer l’image','welcomeclear')],
                 [button('👤 Aperçu membre','member'),button('⬅️ Administration','admin')]])
        elif action=='welcomeclear':
            self.db.set('welcome_photo',''); self.welcome_callback(uid,'welcomeconfig')
        elif action=='welcomelang' and len(parts)==2 and parts[1] in LANGUAGES:
            lang=parts[1]
            text='<b>'+LANGUAGES[lang]+'</b>\n\nAccueil :\n'+html.escape(self.db.setting('welcome_text:'+lang) or '(texte par défaut)')
            rows=[[button('📝 Texte d’accueil','welcometext:'+lang)],[button('⚡ Texte coupe-file commun','localfastpass:'+lang)]]
            rows += [[button('📝 '+g['title'],'localdesc:'+lang+':'+g['id'])] for g in self.db.all("SELECT * FROM groups WHERE role!='portal' ORDER BY title")]
            self.screen(uid,text,rows+[[button('⬅️ Retour','welcomeconfig')]])
        elif action=='welcomephoto':
            self.db.run('UPDATE users SET pending=? WHERE id=?',(data,uid))
            self.screen(uid,'Envoie une photo Telegram pour l’accueil des membres.',[[button('⬅️ Annuler','welcomeconfig')]])
        elif action in ('welcometext','localdesc','localfastpass') and len(parts)>=2 and parts[1] in LANGUAGES:
            if action=='localdesc' and (len(parts)!=3 or not self.group(parts[2])): return True
            self.db.run('UPDATE users SET pending=? WHERE id=?',(data,uid))
            limit=500 if action=='welcometext' else (1800 if action=='localfastpass' else 2500)
            self.screen(uid,f'Envoie le texte en {LANGUAGES[parts[1]]} (maximum {limit} caractères). Un tiret rétablit le texte par défaut.',[[button('⬅️ Annuler','welcomelang:'+parts[1])]])
        return True

    def welcome_input(self,uid,m,pending):
        parts=pending.split(':'); action=parts[0]
        if action not in ('welcometext','welcomephoto','localdesc','localfastpass'): return False
        if uid not in self.owners: return True
        if action=='welcomephoto':
            if not m.get('photo'): raise ValueError('Envoie une photo Telegram.')
            self.db.set('welcome_photo',m['photo'][-1]['file_id'])
            target='welcomeconfig'
        else:
            if len(parts)<2 or parts[1] not in LANGUAGES: return True
            value=m.get('text','').strip()
            limit=500 if action=='welcometext' else (1800 if action=='localfastpass' else 2500)
            if not value or len(value.encode('utf-16-le'))//2>limit: raise ValueError(f'Texte requis, maximum {limit} caractères.')
            key=('welcome_text:'+parts[1] if action=='welcometext' else 'fastpass_description:'+parts[1] if action=='localfastpass' else 'description:'+parts[2]+':'+parts[1])
            if value=='-': self.db.run('DELETE FROM settings WHERE key=?',(key,))
            else: self.db.set(key,value)
            target='welcomelang:'+parts[1]
        self.db.run('UPDATE users SET pending=NULL WHERE id=?',(uid,))
        self.welcome_callback(uid,target)
        return True
