"""Transactional bootstrap and narrowly defined, data-preserving compatibility migration."""
import hashlib
import json
from pathlib import Path
import re
import time

ROOT=Path(__file__).parent/'migrations'
EXPECTED=json.loads((ROOT/'expected.json').read_text())

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
        for path in sorted(ROOT.glob('*.sql')):
            source=path.read_text(); checksum=hashlib.sha256(source.encode()).hexdigest()
            previous=db.one('SELECT checksum FROM network_schema_migrations WHERE version=?',(path.name,))
            if previous and previous['checksum']!=checksum:
                raise SchemaMismatch('Migration modifiée après application : '+path.name)
            if previous: continue
            statements=[s.strip() for s in source.split(';') if s.strip()]
            # Create/verify all tables before creating any index that references a column.
            for sql in sorted(statements,key=lambda s:0 if s.startswith('CREATE TABLE') else 1):
                try: db.run(sql)
                except Exception as error:
                    # Report the DDL object, not raw connection details or row contents.
                    obj=re.search(r'(?:TABLE|INDEX) IF NOT EXISTS (\w+)',sql)
                    raise SchemaMismatch('Échec migration '+path.name+' sur '+(obj.group(1) if obj else 'DDL')+
                                         ' ('+type(error).__name__+'). Transaction annulée ; lancer python -m app.db.doctor.') from None
            db.run('INSERT INTO network_schema_migrations(version,checksum,applied_at) VALUES (?,?,?)',(path.name,checksum,time.time()))
        if inspect(db): raise SchemaMismatch('Schéma incompatible après migration ; transaction annulée.')
