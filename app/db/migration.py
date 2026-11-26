"""Transactional bootstrap and narrowly defined, data-preserving compatibility migration."""
import hashlib
import re
import time

# Embedded deployment resources: importing this module requires Python files only.
# Preserve source strings byte-for-byte: existing database migration checksums depend on them.
MIGRATIONS = (('001_initial.sql',
  'CREATE TABLE IF NOT EXISTS media_tasks (id TEXT PRIMARY KEY, gid TEXT NOT NULL, mid BIGINT NOT '
  'NULL, file_id TEXT NOT NULL, download_id TEXT NOT NULL, kind TEXT NOT NULL, due DOUBLE '
  'PRECISION NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);\n'
  'CREATE TABLE IF NOT EXISTS media_files (file_id TEXT NOT NULL, sha TEXT NOT NULL, gid TEXT NOT '
  'NULL, PRIMARY KEY(file_id,gid));\n'
  'CREATE INDEX IF NOT EXISTS media_sha ON media_files(sha);\n'
  'CREATE TABLE IF NOT EXISTS media_hashes (file_id TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY '
  'KEY(file_id,hash));\n'
  'CREATE INDEX IF NOT EXISTS media_hash_lookup ON media_hashes(hash);\n'
  'CREATE TABLE IF NOT EXISTS hash_bands (hash TEXT NOT NULL, band INTEGER NOT NULL, value TEXT '
  'NOT NULL, PRIMARY KEY(hash,band));\n'
  'CREATE INDEX IF NOT EXISTS band_lookup ON hash_bands(band,value);\n'
  '\n'
  'CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);\n'
  'CREATE TABLE IF NOT EXISTS users (id BIGINT PRIMARY KEY, menu BIGINT, pending TEXT,\n'
  ' banned INTEGER NOT NULL DEFAULT 0, permanent INTEGER NOT NULL DEFAULT 0,\n'
  ' ban_base INTEGER NOT NULL DEFAULT 0);\n'
  'CREATE TABLE IF NOT EXISTS groups (id TEXT PRIMARY KEY, chat BIGINT UNIQUE NOT NULL,\n'
  " title TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'unset', description TEXT NOT NULL DEFAULT "
  "'',\n"
  ' goal INTEGER NOT NULL DEFAULT 5, price INTEGER NOT NULL DEFAULT 0,\n'
  ' monthly INTEGER NOT NULL DEFAULT 0, locker INTEGER NOT NULL DEFAULT 0,\n'
  " threshold INTEGER NOT NULL DEFAULT 100, slot TEXT NOT NULL DEFAULT '22-01',\n"
  ' weekday INTEGER NOT NULL DEFAULT 2, opened INTEGER NOT NULL DEFAULT 0,\n'
  " enabled INTEGER NOT NULL DEFAULT 0, rules TEXT NOT NULL DEFAULT '',\n"
  ' anti INTEGER NOT NULL DEFAULT 0, last_open DOUBLE PRECISION NOT NULL DEFAULT 0);\n'
  'CREATE TABLE IF NOT EXISTS access (uid BIGINT NOT NULL, gid TEXT NOT NULL,\n'
  ' source TEXT NOT NULL, expires DOUBLE PRECISION, revoked INTEGER NOT NULL DEFAULT 0,\n'
  ' base INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(uid,gid));\n'
  'CREATE TABLE IF NOT EXISTS members (uid BIGINT NOT NULL, gid TEXT NOT NULL,\n'
  ' present INTEGER NOT NULL DEFAULT 1, joined DOUBLE PRECISION NOT NULL,\n'
  ' media DOUBLE PRECISION NOT NULL, warned INTEGER NOT NULL DEFAULT 0,\n'
  ' PRIMARY KEY(uid,gid));\n'
  'CREATE TABLE IF NOT EXISTS referrals (invitee BIGINT PRIMARY KEY, inviter BIGINT NOT NULL,\n'
  ' joined DOUBLE PRECISION NOT NULL, present INTEGER NOT NULL DEFAULT 1,\n'
  ' valid INTEGER NOT NULL DEFAULT 0);\n'
  'CREATE TABLE IF NOT EXISTS links (url TEXT PRIMARY KEY, gid TEXT NOT NULL,\n'
  ' uid BIGINT NOT NULL, purpose TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL);\n'
  'CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, uid BIGINT NOT NULL, gid TEXT NOT '
  'NULL,\n'
  ' amount INTEGER NOT NULL, monthly INTEGER NOT NULL, status TEXT NOT NULL,\n'
  ' created DOUBLE PRECISION NOT NULL, invoice TEXT, charge TEXT UNIQUE);\n'
  'CREATE UNIQUE INDEX IF NOT EXISTS one_pending ON orders(uid,gid) WHERE status IN '
  "('pending','checkout');\n"
  'CREATE TABLE IF NOT EXISTS messages (gid TEXT NOT NULL, mid BIGINT NOT NULL,\n'
  ' uid BIGINT NOT NULL, album TEXT, file_id TEXT, created DOUBLE PRECISION NOT NULL,\n'
  ' PRIMARY KEY(gid,mid));\n'
  'CREATE TABLE IF NOT EXISTS notices (gid TEXT NOT NULL, kind TEXT NOT NULL,\n'
  ' mid BIGINT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(gid,kind));\n'
  'CREATE TABLE IF NOT EXISTS strikes (uid BIGINT PRIMARY KEY, count INTEGER NOT NULL);\n'
  'CREATE TABLE IF NOT EXISTS reports (gid TEXT NOT NULL, target TEXT NOT NULL,\n'
  ' reporter BIGINT NOT NULL, PRIMARY KEY(gid,target,reporter));\n'
  'CREATE TABLE IF NOT EXISTS fingerprints (file_id TEXT PRIMARY KEY, blocked INTEGER NOT NULL '
  'DEFAULT 0);\n'
  'CREATE TABLE IF NOT EXISTS seen_media (gid TEXT NOT NULL, file_id TEXT NOT NULL, PRIMARY '
  'KEY(gid,file_id));\n'
  'CREATE TABLE IF NOT EXISTS proof_used (uid BIGINT NOT NULL, gid TEXT NOT NULL, PRIMARY '
  'KEY(uid,gid));\n'
  'CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, method TEXT NOT NULL, payload TEXT NOT '
  'NULL,\n'
  ' due DOUBLE PRECISION NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);\n'
  'CREATE TABLE IF NOT EXISTS sessions (day TEXT PRIMARY KEY, gid TEXT NOT NULL);\n'
  'CREATE TABLE IF NOT EXISTS ads (id TEXT PRIMARY KEY, gid TEXT NOT NULL, text TEXT NOT NULL,\n'
  ' photo TEXT, interval_hours INTEGER NOT NULL DEFAULT 4, enabled INTEGER NOT NULL DEFAULT 1,\n'
  ' next_at DOUBLE PRECISION NOT NULL DEFAULT 0);\n'
  'CREATE TABLE IF NOT EXISTS processed (id BIGINT PRIMARY KEY);\n'
  'CREATE TABLE IF NOT EXISTS audit (id TEXT PRIMARY KEY, actor BIGINT NOT NULL,\n'
  ' action TEXT NOT NULL, created DOUBLE PRECISION NOT NULL);\n'),
 ('002_manual_payments.sql',
  'CREATE TABLE IF NOT EXISTS payment_prices (gid TEXT NOT NULL, method TEXT NOT NULL, cents '
  'INTEGER NOT NULL, PRIMARY KEY(gid,method));\n'
  'CREATE TABLE IF NOT EXISTS manual_orders (id TEXT PRIMARY KEY, uid BIGINT NOT NULL, gid TEXT '
  'NOT NULL, method TEXT NOT NULL, currency TEXT NOT NULL, base INTEGER NOT NULL, fee INTEGER NOT '
  'NULL, total INTEGER NOT NULL, destination TEXT NOT NULL, monthly INTEGER NOT NULL, status TEXT '
  'NOT NULL, created DOUBLE PRECISION NOT NULL, proof_id TEXT, proof_unique TEXT, proof_kind TEXT, '
  'reviewed_by BIGINT, reviewed_at DOUBLE PRECISION);\n'
  'CREATE UNIQUE INDEX IF NOT EXISTS manual_pending ON manual_orders(uid,gid) WHERE status IN '
  "('awaiting_proof','review');\n"
  'CREATE UNIQUE INDEX IF NOT EXISTS manual_unique_proof ON manual_orders(proof_unique) WHERE '
  'proof_unique IS NOT NULL;\n'))

EXPECTED = {'access': {'columns': [{'name': 'uid', 'type': 'BIGINT'},
                        {'name': 'gid', 'type': 'TEXT'},
                        {'name': 'source', 'type': 'TEXT'},
                        {'name': 'expires', 'type': 'DOUBLE PRECISION'},
                        {'name': 'revoked', 'type': 'INTEGER'},
                        {'name': 'base', 'type': 'INTEGER'}]},
 'ads': {'columns': [{'name': 'id', 'type': 'TEXT'},
                     {'name': 'gid', 'type': 'TEXT'},
                     {'name': 'text', 'type': 'TEXT'},
                     {'name': 'photo', 'type': 'TEXT'},
                     {'name': 'interval_hours', 'type': 'INTEGER'},
                     {'name': 'enabled', 'type': 'INTEGER'},
                     {'name': 'next_at', 'type': 'DOUBLE PRECISION'}]},
 'audit': {'columns': [{'name': 'id', 'type': 'TEXT'},
                       {'name': 'actor', 'type': 'BIGINT'},
                       {'name': 'action', 'type': 'TEXT'},
                       {'name': 'created', 'type': 'DOUBLE PRECISION'}]},
 'fingerprints': {'columns': [{'name': 'file_id', 'type': 'TEXT'}, {'name': 'blocked', 'type': 'INTEGER'}]},
 'groups': {'columns': [{'name': 'id', 'type': 'TEXT'},
                        {'name': 'chat', 'type': 'BIGINT'},
                        {'name': 'title', 'type': 'TEXT'},
                        {'name': 'role', 'type': 'TEXT'},
                        {'name': 'description', 'type': 'TEXT'},
                        {'name': 'goal', 'type': 'INTEGER'},
                        {'name': 'price', 'type': 'INTEGER'},
                        {'name': 'monthly', 'type': 'INTEGER'},
                        {'name': 'locker', 'type': 'INTEGER'},
                        {'name': 'threshold', 'type': 'INTEGER'},
                        {'name': 'slot', 'type': 'TEXT'},
                        {'name': 'weekday', 'type': 'INTEGER'},
                        {'name': 'opened', 'type': 'INTEGER'},
                        {'name': 'enabled', 'type': 'INTEGER'},
                        {'name': 'rules', 'type': 'TEXT'},
                        {'name': 'anti', 'type': 'INTEGER'},
                        {'name': 'last_open', 'type': 'DOUBLE PRECISION'}]},
 'hash_bands': {'columns': [{'name': 'hash', 'type': 'TEXT'},
                            {'name': 'band', 'type': 'INTEGER'},
                            {'name': 'value', 'type': 'TEXT'}]},
 'jobs': {'columns': [{'name': 'id', 'type': 'TEXT'},
                      {'name': 'method', 'type': 'TEXT'},
                      {'name': 'payload', 'type': 'TEXT'},
                      {'name': 'due', 'type': 'DOUBLE PRECISION'},
                      {'name': 'attempts', 'type': 'INTEGER'}]},
 'links': {'columns': [{'name': 'url', 'type': 'TEXT'},
                       {'name': 'gid', 'type': 'TEXT'},
                       {'name': 'uid', 'type': 'BIGINT'},
                       {'name': 'purpose', 'type': 'TEXT'},
                       {'name': 'expires', 'type': 'DOUBLE PRECISION'}]},
 'manual_orders': {'columns': [{'name': 'id', 'type': 'TEXT'},
                               {'name': 'uid', 'type': 'BIGINT'},
                               {'name': 'gid', 'type': 'TEXT'},
                               {'name': 'method', 'type': 'TEXT'},
                               {'name': 'currency', 'type': 'TEXT'},
                               {'name': 'base', 'type': 'INTEGER'},
                               {'name': 'fee', 'type': 'INTEGER'},
                               {'name': 'total', 'type': 'INTEGER'},
                               {'name': 'destination', 'type': 'TEXT'},
                               {'name': 'monthly', 'type': 'INTEGER'},
                               {'name': 'status', 'type': 'TEXT'},
                               {'name': 'created', 'type': 'DOUBLE PRECISION'},
                               {'name': 'proof_id', 'type': 'TEXT'},
                               {'name': 'proof_unique', 'type': 'TEXT'},
                               {'name': 'proof_kind', 'type': 'TEXT'},
                               {'name': 'reviewed_by', 'type': 'BIGINT'},
                               {'name': 'reviewed_at', 'type': 'DOUBLE PRECISION'}]},
 'media_files': {'columns': [{'name': 'file_id', 'type': 'TEXT'},
                             {'name': 'sha', 'type': 'TEXT'},
                             {'name': 'gid', 'type': 'TEXT'}]},
 'media_hashes': {'columns': [{'name': 'file_id', 'type': 'TEXT'}, {'name': 'hash', 'type': 'TEXT'}]},
 'media_tasks': {'columns': [{'name': 'id', 'type': 'TEXT'},
                             {'name': 'gid', 'type': 'TEXT'},
                             {'name': 'mid', 'type': 'BIGINT'},
                             {'name': 'file_id', 'type': 'TEXT'},
                             {'name': 'download_id', 'type': 'TEXT'},
                             {'name': 'kind', 'type': 'TEXT'},
                             {'name': 'due', 'type': 'DOUBLE PRECISION'},
                             {'name': 'attempts', 'type': 'INTEGER'}]},
 'members': {'columns': [{'name': 'uid', 'type': 'BIGINT'},
                         {'name': 'gid', 'type': 'TEXT'},
                         {'name': 'present', 'type': 'INTEGER'},
                         {'name': 'joined', 'type': 'DOUBLE PRECISION'},
                         {'name': 'media', 'type': 'DOUBLE PRECISION'},
                         {'name': 'warned', 'type': 'INTEGER'}]},
 'messages': {'columns': [{'name': 'gid', 'type': 'TEXT'},
                          {'name': 'mid', 'type': 'BIGINT'},
                          {'name': 'uid', 'type': 'BIGINT'},
                          {'name': 'album', 'type': 'TEXT'},
                          {'name': 'file_id', 'type': 'TEXT'},
                          {'name': 'created', 'type': 'DOUBLE PRECISION'}]},
 'notices': {'columns': [{'name': 'gid', 'type': 'TEXT'},
                         {'name': 'kind', 'type': 'TEXT'},
                         {'name': 'mid', 'type': 'BIGINT'},
                         {'name': 'body', 'type': 'TEXT'}]},
 'orders': {'columns': [{'name': 'id', 'type': 'TEXT'},
                        {'name': 'uid', 'type': 'BIGINT'},
                        {'name': 'gid', 'type': 'TEXT'},
                        {'name': 'amount', 'type': 'INTEGER'},
                        {'name': 'monthly', 'type': 'INTEGER'},
                        {'name': 'status', 'type': 'TEXT'},
                        {'name': 'created', 'type': 'DOUBLE PRECISION'},
                        {'name': 'invoice', 'type': 'TEXT'},
                        {'name': 'charge', 'type': 'TEXT'}]},
 'payment_prices': {'columns': [{'name': 'gid', 'type': 'TEXT'},
                                {'name': 'method', 'type': 'TEXT'},
                                {'name': 'cents', 'type': 'INTEGER'}]},
 'processed': {'columns': [{'name': 'id', 'type': 'BIGINT'}]},
 'proof_used': {'columns': [{'name': 'uid', 'type': 'BIGINT'}, {'name': 'gid', 'type': 'TEXT'}]},
 'referrals': {'columns': [{'name': 'invitee', 'type': 'BIGINT'},
                           {'name': 'inviter', 'type': 'BIGINT'},
                           {'name': 'joined', 'type': 'DOUBLE PRECISION'},
                           {'name': 'present', 'type': 'INTEGER'},
                           {'name': 'valid', 'type': 'INTEGER'}]},
 'reports': {'columns': [{'name': 'gid', 'type': 'TEXT'},
                         {'name': 'target', 'type': 'TEXT'},
                         {'name': 'reporter', 'type': 'BIGINT'}]},
 'seen_media': {'columns': [{'name': 'gid', 'type': 'TEXT'}, {'name': 'file_id', 'type': 'TEXT'}]},
 'sessions': {'columns': [{'name': 'day', 'type': 'TEXT'}, {'name': 'gid', 'type': 'TEXT'}]},
 'settings': {'columns': [{'name': 'key', 'type': 'TEXT'}, {'name': 'value', 'type': 'TEXT'}]},
 'strikes': {'columns': [{'name': 'uid', 'type': 'BIGINT'}, {'name': 'count', 'type': 'INTEGER'}]},
 'users': {'columns': [{'name': 'id', 'type': 'BIGINT'},
                       {'name': 'menu', 'type': 'BIGINT'},
                       {'name': 'pending', 'type': 'TEXT'},
                       {'name': 'banned', 'type': 'INTEGER'},
                       {'name': 'permanent', 'type': 'INTEGER'},
                       {'name': 'ban_base', 'type': 'INTEGER'}]}}


# Additive migration: previously applied migration strings are unchanged.
MIGRATIONS += (("003_metrics.sql", "CREATE TABLE IF NOT EXISTS metric_events (id TEXT PRIMARY KEY, kind TEXT NOT NULL, uid BIGINT, gid TEXT, created DOUBLE PRECISION NOT NULL); CREATE INDEX IF NOT EXISTS metric_time_kind ON metric_events(created,kind); CREATE INDEX IF NOT EXISTS metric_group_time ON metric_events(gid,created);"),)
EXPECTED['metric_events']={'columns':[{'name':name,'type':kind} for name,kind in [('id','TEXT'),('kind','TEXT'),('uid','BIGINT'),('gid','TEXT'),('created','DOUBLE PRECISION')]]}

MIGRATIONS += (("004_member_workflows.sql", "CREATE TABLE IF NOT EXISTS referral_targets (invitee BIGINT PRIMARY KEY, gid TEXT NOT NULL); CREATE TABLE IF NOT EXISTS target_links (url TEXT PRIMARY KEY, gid TEXT NOT NULL); CREATE TABLE IF NOT EXISTS bug_tickets (id TEXT PRIMARY KEY, uid BIGINT NOT NULL, gid TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created DOUBLE PRECISION NOT NULL, reviewed_by BIGINT, reviewed_at DOUBLE PRECISION); CREATE TABLE IF NOT EXISTS group_launches (gid TEXT PRIMARY KEY, created DOUBLE PRECISION NOT NULL); CREATE TABLE IF NOT EXISTS launch_reads (uid BIGINT NOT NULL, gid TEXT NOT NULL, PRIMARY KEY(uid,gid)); CREATE INDEX IF NOT EXISTS referral_target_group ON referral_targets(gid); CREATE INDEX IF NOT EXISTS bug_status_time ON bug_tickets(status,created); CREATE INDEX IF NOT EXISTS bug_user_group ON bug_tickets(uid,gid,status);"),)
EXPECTED['referral_targets']={'columns': [{'name': 'invitee', 'type': 'BIGINT'}, {'name': 'gid', 'type': 'TEXT'}]}
EXPECTED['target_links']={'columns': [{'name': 'url', 'type': 'TEXT'}, {'name': 'gid', 'type': 'TEXT'}]}
EXPECTED['bug_tickets']={'columns': [{'name': 'id', 'type': 'TEXT'}, {'name': 'uid', 'type': 'BIGINT'}, {'name': 'gid', 'type': 'TEXT'}, {'name': 'body', 'type': 'TEXT'}, {'name': 'status', 'type': 'TEXT'}, {'name': 'created', 'type': 'DOUBLE PRECISION'}, {'name': 'reviewed_by', 'type': 'BIGINT'}, {'name': 'reviewed_at', 'type': 'DOUBLE PRECISION'}]}
EXPECTED['group_launches']={'columns': [{'name': 'gid', 'type': 'TEXT'}, {'name': 'created', 'type': 'DOUBLE PRECISION'}]}
EXPECTED['launch_reads']={'columns': [{'name': 'uid', 'type': 'BIGINT'}, {'name': 'gid', 'type': 'TEXT'}]}

MIGRATIONS += (("005_fast_dispatch.sql", "CREATE TABLE IF NOT EXISTS update_inbox (id BIGINT PRIMARY KEY, lane TEXT NOT NULL, payload TEXT NOT NULL, created DOUBLE PRECISION NOT NULL, state TEXT NOT NULL DEFAULT 'ready', attempts INTEGER NOT NULL DEFAULT 0, due DOUBLE PRECISION NOT NULL DEFAULT 0); CREATE INDEX IF NOT EXISTS inbox_lane_state ON update_inbox(lane,state,id); CREATE INDEX IF NOT EXISTS referral_inviter_valid ON referrals(inviter,valid); CREATE INDEX IF NOT EXISTS jobs_due ON jobs(due); CREATE INDEX IF NOT EXISTS members_activity ON members(present,media);"),)
EXPECTED["update_inbox"]={'columns': [{'name': 'id', 'type': 'BIGINT'}, {'name': 'lane', 'type': 'TEXT'}, {'name': 'payload', 'type': 'TEXT'}, {'name': 'created', 'type': 'DOUBLE PRECISION'}, {'name': 'state', 'type': 'TEXT'}, {'name': 'attempts', 'type': 'INTEGER'}, {'name': 'due', 'type': 'DOUBLE PRECISION'}]}

MIGRATIONS += (("006_priority_actors.sql", 'CREATE TABLE IF NOT EXISTS update_actors (id BIGINT PRIMARY KEY, actor TEXT NOT NULL); CREATE INDEX IF NOT EXISTS actor_order ON update_actors(actor,id); CREATE INDEX IF NOT EXISTS ready_updates ON update_inbox(lane,state,due,id); CREATE INDEX IF NOT EXISTS media_due ON media_tasks(due); CREATE INDEX IF NOT EXISTS message_album ON messages(gid,album); CREATE TABLE IF NOT EXISTS failed_actions (id TEXT PRIMARY KEY, kind TEXT NOT NULL, detail TEXT NOT NULL, created DOUBLE PRECISION NOT NULL);'),)
EXPECTED["update_actors"]={"columns":[{"name":"id","type":"BIGINT"},{"name":"actor","type":"TEXT"}]}
EXPECTED["failed_actions"]={"columns":[{"name":"id","type":"TEXT"},{"name":"kind","type":"TEXT"},{"name":"detail","type":"TEXT"},{"name":"created","type":"DOUBLE PRECISION"}]}


MIGRATIONS += (("007_recovery.sql", "CREATE TABLE IF NOT EXISTS group_pauses (chat BIGINT PRIMARY KEY, gid TEXT NOT NULL, was_enabled INTEGER NOT NULL, reason TEXT NOT NULL, created DOUBLE PRECISION NOT NULL); CREATE TABLE IF NOT EXISTS portal_moves (id TEXT PRIMARY KEY, gid TEXT NOT NULL, old_chat BIGINT NOT NULL, new_chat BIGINT NOT NULL, created DOUBLE PRECISION NOT NULL); CREATE TABLE IF NOT EXISTS portal_recipients (move_id TEXT NOT NULL, uid BIGINT NOT NULL, state TEXT NOT NULL DEFAULT 'ready', attempts INTEGER NOT NULL DEFAULT 0, due DOUBLE PRECISION NOT NULL DEFAULT 0, PRIMARY KEY(move_id,uid)); CREATE TABLE IF NOT EXISTS portal_targets (move_id TEXT NOT NULL, uid BIGINT NOT NULL, target TEXT NOT NULL, url TEXT, PRIMARY KEY(move_id,uid,target)); CREATE TABLE IF NOT EXISTS media_failures (id TEXT PRIMARY KEY, payload TEXT NOT NULL, category TEXT NOT NULL, detail TEXT NOT NULL, created DOUBLE PRECISION NOT NULL); CREATE INDEX IF NOT EXISTS portal_dispatch ON portal_recipients(state,due);"),)
EXPECTED['group_pauses']={'columns': [{'name': 'chat', 'type': 'BIGINT'}, {'name': 'gid', 'type': 'TEXT'}, {'name': 'was_enabled', 'type': 'INTEGER'}, {'name': 'reason', 'type': 'TEXT'}, {'name': 'created', 'type': 'DOUBLE PRECISION'}]}
EXPECTED['portal_moves']={'columns': [{'name': 'id', 'type': 'TEXT'}, {'name': 'gid', 'type': 'TEXT'}, {'name': 'old_chat', 'type': 'BIGINT'}, {'name': 'new_chat', 'type': 'BIGINT'}, {'name': 'created', 'type': 'DOUBLE PRECISION'}]}
EXPECTED['portal_recipients']={'columns': [{'name': 'move_id', 'type': 'TEXT'}, {'name': 'uid', 'type': 'BIGINT'}, {'name': 'state', 'type': 'TEXT'}, {'name': 'attempts', 'type': 'INTEGER'}, {'name': 'due', 'type': 'DOUBLE PRECISION'}]}
EXPECTED['portal_targets']={'columns': [{'name': 'move_id', 'type': 'TEXT'}, {'name': 'uid', 'type': 'BIGINT'}, {'name': 'target', 'type': 'TEXT'}, {'name': 'url', 'type': 'TEXT'}]}
EXPECTED['media_failures']={'columns': [{'name': 'id', 'type': 'TEXT'}, {'name': 'payload', 'type': 'TEXT'}, {'name': 'category', 'type': 'TEXT'}, {'name': 'detail', 'type': 'TEXT'}, {'name': 'created', 'type': 'DOUBLE PRECISION'}]}

# New ledgers are append-only; earlier deployed checksums remain unchanged.
MIGRATIONS += (("008_fair_access.sql", "CREATE TABLE IF NOT EXISTS activity_clocks (uid BIGINT NOT NULL, gid TEXT NOT NULL, media DOUBLE PRECISION NOT NULL, joined DOUBLE PRECISION NOT NULL, checked DOUBLE PRECISION NOT NULL, seconds DOUBLE PRECISION NOT NULL DEFAULT 0, eligible INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(uid,gid)); CREATE TABLE IF NOT EXISTS rehabilitation (uid BIGINT NOT NULL, gid TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'review', created DOUBLE PRECISION NOT NULL, PRIMARY KEY(uid,gid)); CREATE TABLE IF NOT EXISTS referral_origins (invitee BIGINT PRIMARY KEY, gid TEXT NOT NULL); CREATE TABLE IF NOT EXISTS referral_reserves (invitee BIGINT PRIMARY KEY, uid BIGINT NOT NULL); CREATE INDEX IF NOT EXISTS reserve_user ON referral_reserves(uid,invitee); CREATE TABLE IF NOT EXISTS feature_notifications (uid BIGINT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'ready', due DOUBLE PRECISION NOT NULL DEFAULT 0);"),)
for _table,_columns in {
    'activity_clocks':[('uid','BIGINT'),('gid','TEXT'),('media','DOUBLE PRECISION'),('joined','DOUBLE PRECISION'),('checked','DOUBLE PRECISION'),('seconds','DOUBLE PRECISION'),('eligible','INTEGER')],
    'rehabilitation':[('uid','BIGINT'),('gid','TEXT'),('state','TEXT'),('created','DOUBLE PRECISION')],
    'referral_origins':[('invitee','BIGINT'),('gid','TEXT')],
    'referral_reserves':[('invitee','BIGINT'),('uid','BIGINT')],
    'feature_notifications':[('uid','BIGINT'),('state','TEXT'),('due','DOUBLE PRECISION')],
}.items(): EXPECTED[_table]={'columns':[{'name':n,'type':t} for n,t in _columns]}

class SchemaMismatch(RuntimeError):
    pass


def quote(name):
    # All identifiers used in DDL come from our checked-in schema, never user input.
    if not re.fullmatch(r'[a-z_][a-z_0-9]*',name): raise ValueError('Invalid SQL identifier')
    return '"'+name+'"'


def columns(db,table):
    if db.sqlite:
        return {r['name']:r['type'].upper() for r in db.all('PRAGMA table_info('+quote(table)+')')}
    return {r['column_name']:r['data_type'].upper() for r in db.all(
        'SELECT column_name,data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?',(table,))}


def category(type_name):
    if type_name in ('TEXT','CHARACTER VARYING','VARCHAR'): return 'text'
    if type_name in ('BIGINT','INTEGER','SMALLINT','INT'): return 'integer'
    if type_name in ('DOUBLE PRECISION','REAL','FLOAT','NUMERIC'): return 'float'
    return type_name


def inspect(db):
    result=[]
    for table,spec in EXPECTED.items():
        actual=columns(db,table)
        if not actual: continue
        missing=[c['name'] for c in spec['columns'] if c['name'] not in actual]
        types=[c['name'] for c in spec['columns'] if c['name'] in actual and category(c['type'])!=category(actual[c['name']])]
        if missing or types:
            result.append({'table':table,'missing_columns':missing,'incompatible_types':types,
                           'existing_columns':actual,'rows':db.one('SELECT COUNT(*) AS n FROM '+quote(table))['n']})
    return result


def plan_repairs(db):
    actions=[]; blockers=[]
    for issue in inspect(db):
        table=issue['table']; actual=issue['existing_columns']
        # Never guess a logical group identifier from a Telegram chat identifier.
        if issue['missing_columns']==['gid'] and not issue['incompatible_types']:
            if 'group_id' in actual and category(actual['group_id'])=='text':
                groupcols=columns(db,'groups')
                if issue['rows']==0:
                    valid=True
                elif groupcols.get('id') and category(groupcols['id'])=='text':
                    valid=not db.one('SELECT 1 AS bad FROM '+quote(table)+' t WHERE t.group_id IS NULL OR NOT EXISTS (SELECT 1 FROM groups g WHERE g.id=t.group_id) LIMIT 1')
                else: valid=False
                if valid:
                    actions.append('ALTER TABLE '+quote(table)+' RENAME COLUMN "group_id" TO "gid"')
                    continue
            if issue['rows']==0 and 'group_id' not in actual:
                actions.append('ALTER TABLE '+quote(table)+' ADD COLUMN "gid" TEXT NOT NULL')
                continue
        blockers.append(issue)
    if blockers:
        details='; '.join(f'{i["table"]}: colonnes manquantes={i["missing_columns"]}, types incompatibles={i["incompatible_types"]}, lignes={i["rows"]}' for i in blockers)
        raise SchemaMismatch('Schéma existant incompatible. Aucune modification appliquée. '+details+
                             '. Exécuter python -m app.db.doctor et fournir le diagnostic. Ne pas supprimer la base.')
    return actions


def migrate(db):
    with db.transaction():
        if not db.sqlite:
            db.run('SELECT pg_advisory_xact_lock(731482032)')
        actions=plan_repairs(db)
        for sql in actions: db.run(sql)
        db.run('CREATE TABLE IF NOT EXISTS network_schema_migrations (version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at DOUBLE PRECISION NOT NULL)')
        for version, source in MIGRATIONS:
            checksum=hashlib.sha256(source.encode()).hexdigest()
            previous=db.one('SELECT checksum FROM network_schema_migrations WHERE version=?',(version,))
            if previous and previous['checksum']!=checksum:
                raise SchemaMismatch('Migration modifiée après application : '+version)
            if previous: continue
            statements=[s.strip() for s in source.split(';') if s.strip()]
            # Create/verify all tables before creating any index that references a column.
            for sql in sorted(statements,key=lambda s:0 if s.startswith('CREATE TABLE') else 1):
                try: db.run(sql)
                except Exception as error:
                    # Report the DDL object, not raw connection details or row contents.
                    obj=re.search(r'(?:TABLE|INDEX) IF NOT EXISTS (\w+)',sql)
                    raise SchemaMismatch('Échec migration '+version+' sur '+(obj.group(1) if obj else 'DDL')+
                                         ' ('+type(error).__name__+'). Transaction annulée ; lancer python -m app.db.doctor.') from None
            db.run('INSERT INTO network_schema_migrations(version,checksum,applied_at) VALUES (?,?,?)',(version,checksum,time.time()))
        if inspect(db): raise SchemaMismatch('Schéma incompatible après migration ; transaction annulée.')
