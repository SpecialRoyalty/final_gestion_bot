"""Portal campaigns and temporary opening announcements."""
import time
import math
from app.keyboards.inline import button
from app.services.telegram import APIError, DeferredRequest, deletion_result

class Promotions:
    def remove_notice_now(self,g,kind):
        old=self.db.one('SELECT mid FROM notices WHERE gid=? AND kind=?',(g['id'],kind))
        if not old:return
        key=f'notice-delete:{g["chat"]}:{old["mid"]}'
        if self.db.one('SELECT 1 FROM failed_actions WHERE id=?',(key,)):raise DeferredRequest(300)
        try:self.api.call('deleteMessage',chat_id=g['chat'],message_id=old['mid'])
        except APIError as error:
            if deletion_result(error)=='terminal':self.failed_action('notice_delete',error,key)
            if deletion_result(error)!='gone':raise
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
        groups=self.db.all('SELECT * FROM groups WHERE enabled=1')
        windows=self.scheduled(groups,now)
        for g in groups:
            if g['role'] not in ('chat','free','always'):continue
            active=next(((a,b) for a,b in windows.get(g['id'],[]) if a<=now<b),None)
            if g['role']!='always' and not active:continue
            kind='opening:'+g['id'];visible.add(kind)
            text='🟢 '+g['title']+' est ouvert !'
            if active:
                # A stable 30-minute bucket, independent of the locker's actual permissions.
                minutes=max(30,math.ceil((active[1]-now)/1800)*30)
                hours,rest=divmod(minutes,60)
                duration=(str(hours)+' h'+(' '+str(rest) if rest else '')) if hours else str(rest)+' min'
                text+=' Il fermera dans '+duration+'.'
            else:text+=' Accès 24 h/24.'
            if g['role']=='free':
                rows=[[button('🚪 Rejoindre le groupe gratuit',url=self.free_portal_link(g))]]
            else:
                rows=[[button('🚪 Voir mon accès',url=self.deep('g_'+g['id']))],
                      [button('🔗 Inviter mes amis',url=self.deep('invite_'+g['id']))]]
            self.notice(portal,kind,text,rows)
        for old in self.db.all("SELECT kind FROM notices WHERE gid=? AND kind LIKE ?",(portal['id'],'opening:%')):
            if old['kind'] not in visible:self.remove_notice_now(portal,old['kind'])
