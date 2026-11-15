import html
import uuid
from app.keyboards.inline import button

class Router:
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

