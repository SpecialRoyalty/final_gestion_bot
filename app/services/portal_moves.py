"""Durable portal replacement campaigns, separate from interaction/moderation workers."""
import time
import uuid
from app.keyboards.inline import button,keyboard
from app.services.telegram import APIError,NetworkError,DeferredRequest,blocked_recipient

class PortalMoves:
    def prepare_portal_move(self,g,new_chat):
        # Called inside the group replacement transaction, before deleting old links.
        move=uuid.uuid4().hex
        self.db.run("UPDATE portal_recipients SET state='superseded' WHERE state IN ('ready','sending') AND move_id IN (SELECT id FROM portal_moves WHERE gid=?)",(g['id'],))
        self.db.run('INSERT INTO portal_moves(id,gid,old_chat,new_chat,created) VALUES (?,?,?,?,?)',(move,g['id'],g['chat'],new_chat,time.time()))
        self.db.run("INSERT INTO portal_recipients(move_id,uid,state) SELECT ?,id,CASE WHEN menu IS NULL OR menu=0 THEN 'unreachable' ELSE 'ready' END FROM users",(move,))
        self.db.run('INSERT INTO portal_targets(move_id,uid,target) SELECT DISTINCT ?,l.uid,t.gid FROM links l JOIN target_links t ON t.url=l.url WHERE l.gid=?',(move,g['id']))
        # Pending referral presence was observed in the old portal, not the new one.
        self.db.run('UPDATE referrals SET present=0 WHERE valid=0')
        return move

    def portal_move_step(self):
        row=self.db.one("SELECT r.*,m.gid,m.new_chat FROM portal_recipients r JOIN portal_moves m ON m.id=r.move_id WHERE r.state='ready' AND r.due<=? ORDER BY m.created,r.uid LIMIT 1",(time.time(),))
        if not row:return False
        uid=row['uid'];ident=(row['move_id'],uid);g=self.group(row['gid'])
        if not g or g['chat']!=row['new_chat']:
            self.db.run("UPDATE portal_recipients SET state='superseded' WHERE move_id=? AND uid=?",ident);return True
        if not g['enabled']:
            self.db.run('UPDATE portal_recipients SET due=? WHERE move_id=? AND uid=?',(time.time()+30,*ident));return True
        if self.db.setting('dm_blocked:'+str(uid)) or self.user(uid)['permanent']:
            self.db.run("UPDATE portal_recipients SET state='unreachable' WHERE move_id=? AND uid=?",ident);return True
        sending=False
        try:
            target=self.db.one('SELECT target FROM portal_targets WHERE move_id=? AND uid=? AND url IS NULL ORDER BY target LIMIT 1',ident)
            if target:
                goal=self.group(target['target'])
                url=self.target_link(uid,goal,g) if goal and goal['enabled'] and goal['role'] in ('chat','always') else ''
                self.db.run('UPDATE portal_targets SET url=? WHERE move_id=? AND uid=? AND target=?',(url,*ident,target['target']))
                return True
            text=self.tr(uid,'📢 Le portail a changé. Tes invitations validées et tes accès sont conservés. Tes nouveaux liens sont disponibles dans « Inviter mes amis ». Remplace les anciens liens dans tes partages.')
            rows=[[button(self.tr(uid,'🔗 Inviter mes amis'),url=self.deep('invite'))],[button(self.tr(uid,'⬅️ Accueil'),url=self.deep())]]
            self.db.run("UPDATE portal_recipients SET state='sending' WHERE move_id=? AND uid=?",ident);sending=True
            self.api.call('sendMessage',chat_id=uid,text=text,reply_markup=keyboard(*rows))
            self.db.run("UPDATE portal_recipients SET state='sent' WHERE move_id=? AND uid=?",ident)
        except Exception as error:
            if blocked_recipient(error):
                self.db.set('dm_blocked:'+str(uid),time.time());state='unreachable'
            elif sending and (isinstance(error,NetworkError) or isinstance(error,APIError) and error.code>=500):
                # Telegram may have accepted the message: no blind automatic resend.
                state='uncertain'
            elif isinstance(error,DeferredRequest) or isinstance(error,APIError) and error.code==429:state='ready'
            elif row['attempts']+1>=5:state='failed'
            else:state='ready'
            attempts=row['attempts']+(0 if isinstance(error,DeferredRequest) else 1)
            self.db.run('UPDATE portal_recipients SET state=?,attempts=?,due=? WHERE move_id=? AND uid=?',
                        (state,attempts,time.time()+max(getattr(error,'retry',0),1 if isinstance(error,DeferredRequest) else min(300,2**min(attempts,8))),*ident))
            if state not in ('unreachable','uncertain'):self.record_failure('portal_move',error)
        return True
