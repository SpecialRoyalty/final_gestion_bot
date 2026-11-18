"""Portal campaigns and temporary opening announcements."""
import time
from app.keyboards.inline import button
from app.services.telegram import APIError

class Promotions:
    def remove_notice_now(self,g,kind):
        old=self.db.one('SELECT mid FROM notices WHERE gid=? AND kind=?',(g['id'],kind))
        if not old:return
        try:self.api.call('deleteMessage',chat_id=g['chat'],message_id=old['mid'])
        except APIError as error:
            if 'message to delete not found' not in str(error):raise
        self.db.run('DELETE FROM notices WHERE gid=? AND kind=?',(g['id'],kind))

    def free_portal_link(self,g):
        row=self.db.one("SELECT url FROM links WHERE gid=? AND purpose='public_free' AND expires>?",(g['id'],time.time()+60))
        if row:return row['url']
        expires=int(time.time()+86400)
        result=self.api.call('createChatInviteLink',chat_id=g['chat'],name='Portail · gratuit',creates_join_request=True,expire_date=expires)
        self.db.run("INSERT INTO links(url,gid,uid,purpose,expires) VALUES (?,?,0,'public_free',?)",(result['invite_link'],g['id'],expires))
        return result['invite_link']

    def portal_promotions(self,portal,now):
        visible=set()
        for g in self.db.all("SELECT * FROM groups WHERE enabled=1 AND role IN ('chat','free','always') ORDER BY id"):
            waiting=float(self.db.setting('waiting:'+g['id'],'0'))>now
            if not g['opened'] and not waiting:continue
            kind='opening:'+g['id'];visible.add(kind)
            if g['opened']:
                text='🟢 '+g['title']+' est ouvert ! Rejoignez le groupe.'
            else:
                text='🟠 '+g['title']+' attend '+str(g['threshold'])+' membres pour ouvrir.\nRejoignez-le et invitez vos amis pour atteindre le seuil.'
            if g['role']=='free':
                rows=[[button('🚪 Rejoindre le groupe gratuit',url=self.free_portal_link(g))]]
            else:
                rows=[[button('🚪 Voir mon accès',url=self.deep('g_'+g['id']))],
                      [button('🔗 Inviter mes amis',url=self.deep('invite_'+g['id']))]]
            self.notice(portal,kind,text,rows)
        for old in self.db.all("SELECT kind FROM notices WHERE gid=? AND kind LIKE 'opening:%'",(portal['id'],)):
            if old['kind'] not in visible:self.remove_notice_now(portal,old['kind'])
