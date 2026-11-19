"""Shared links: -1 means no expiry; 0 remains explicitly revoked locally."""
import time

PERMANENT_LINK=-1

def link_valid(link,now=None):
    return bool(link and (link['expires']==PERMANENT_LINK or link['expires']>(time.time() if now is None else now)))

class InviteLinks:
    def make_link_permanent(self,link,group):
        if link['expires']==PERMANENT_LINK:return link['url']
        if not link_valid(link):raise ValueError('Ce lien a expiré. Récupère une nouvelle invitation dans le bot.')
        args=dict(chat_id=group['chat'],invite_link=link['url'],expire_date=0)
        if link['purpose']=='public_free':args['creates_join_request']=True
        result=self.api.call('editChatInviteLink',**args)
        if result.get('invite_link')!=link['url'] or result.get('is_revoked') or result.get('expire_date'):
            raise ValueError('La prolongation du lien n’a pas été confirmée par Telegram.')
        # Never revive a locally revoked link or a link from a replaced chat.
        self.db.run('UPDATE links SET expires=? WHERE url=? AND expires=? AND EXISTS (SELECT 1 FROM groups WHERE id=? AND chat=?)',
                    (PERMANENT_LINK,link['url'],link['expires'],group['id'],group['chat']))
        return link['url']

    def task_permalinks(self,now):
        # One existing link per cycle: no migration burst on Telegram or the button workers.
        link=self.db.one("SELECT l.* FROM links l JOIN groups g ON g.id=l.gid LEFT JOIN settings retry ON retry.key=('permalink_retry:' || l.url) WHERE g.enabled=1 AND l.expires>? AND COALESCE(CAST(retry.value AS DOUBLE PRECISION),0)<=? AND ((l.purpose='referral' AND g.role='portal' AND EXISTS (SELECT 1 FROM target_links t WHERE t.url=l.url)) OR (l.purpose='public_free' AND g.role='free')) ORDER BY l.expires,l.url LIMIT 1",(now,now))
        if not link:return
        try:self.make_link_permanent(link,self.group(link['gid']))
        except Exception as error:
            self.db.set('permalink_retry:'+link['url'],time.time()+max(300,getattr(error,'retry',0)))
            raise
        self.db.run('DELETE FROM settings WHERE key=?',('permalink_retry:'+link['url'],))
