"""Durable notifications for invitation goals, independent of private panel refreshes."""
import time
from app.keyboards.inline import button, keyboard
from app.utils.core import active_access

UNLOCK_TEXT='🎉 Ton accès à « {title} » est débloqué !\n\nTon objectif d’invitations est atteint. Retrouve ton accès avec le bouton ci-dessous. La participation reste soumise aux horaires et au locker du groupe.'

class Unlocks:
    def task_unlocks(self,now):
        # Aggregate once; bounded catch-up includes people who never refreshed their panel.
        rows=self.db.all("""WITH earned AS (
            SELECT r.inviter AS uid,t.gid,COUNT(*) AS n FROM referrals r
            JOIN referral_targets t ON t.invitee=r.invitee WHERE r.valid=1 GROUP BY r.inviter,t.gid
            UNION ALL
            SELECT uid,gid,5*COUNT(*) AS n FROM bug_tickets WHERE status='approved' GROUP BY uid,gid
        ), totals AS (SELECT uid,gid,SUM(n) AS n FROM earned GROUP BY uid,gid)
        SELECT t.uid,t.gid FROM totals t JOIN groups g ON g.id=t.gid JOIN users u ON u.id=t.uid
        LEFT JOIN access a ON a.uid=t.uid AND a.gid=t.gid
        WHERE g.enabled=1 AND g.role IN ('chat','always') AND g.goal>0 AND u.banned=0 AND u.permanent=0
        AND t.n-COALESCE(a.base,0)>=g.goal
        AND (a.uid IS NULL OR a.revoked=1 OR a.expires<=? OR a.source='referral')
        AND NOT EXISTS (SELECT 1 FROM unlock_notifications n WHERE n.uid=t.uid AND n.gid=t.gid AND n.base=COALESCE(a.base,0))
        ORDER BY t.uid,t.gid LIMIT 50""",(now,))
        for row in rows:
            with self.db.transaction():
                self.lock_inviter(row['uid'])
                self.unlock_goal(row['uid'],row['gid'])

    def unlock_goal(self,uid,gid):
        """Caller owns inviter lock/transaction. Recheck before granting and recording delivery."""
        g=self.group(gid);u=self.user(uid);a=self.access(uid,gid);now=time.time()
        if not g or not g['enabled'] or g['role'] not in ('chat','always') or g['goal']<=0 or u['banned'] or u['permanent']:return
        base=a['base'] if a else 0
        if self.count(uid,gid)-base<g['goal']:return
        if active_access(a,now) and a['source']!='referral':return
        if not active_access(a,now):self.grant(uid,g,'referral')
        inserted=self.db.run('INSERT INTO unlock_notifications(uid,gid,base,created) VALUES (?,?,?,?) ON CONFLICT DO NOTHING',(uid,gid,base,now)).rowcount
        if not inserted:return
        # Already participating members do not need a catch-up notification.
        member=self.db.one('SELECT present FROM members WHERE uid=? AND gid=?',(uid,gid))
        if member and member['present']:return
        self.enqueue('sendMessage',dict(chat_id=uid,text=self.tr(uid,UNLOCK_TEXT).format(title=g['title']),
            reply_markup=keyboard([button(self.tr(uid,'🚪 Retrouver mon accès'),url=self.deep('g_'+gid))]),
            _unlock={'gid':gid,'base':base}))

    def unlock_delivery_valid(self,uid,proof):
        g=self.group(proof['gid']);a=self.access(uid,proof['gid'])
        member=self.db.one('SELECT present FROM members WHERE uid=? AND gid=?',(uid,proof['gid']))
        return bool(g and g['enabled'] and g['role'] in ('chat','always') and a and a['base']==proof['base'] and
                    a['source']=='referral' and self.entitled(uid,g) and not (member and member['present']))
