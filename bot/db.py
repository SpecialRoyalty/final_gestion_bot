import sqlite3
from contextlib import contextmanager

SCHEMA = '''
CREATE TABLE IF NOT EXISTS media_tasks (id TEXT PRIMARY KEY, gid TEXT NOT NULL, mid BIGINT NOT NULL, file_id TEXT NOT NULL, download_id TEXT NOT NULL, kind TEXT NOT NULL, due DOUBLE PRECISION NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS media_files (file_id TEXT NOT NULL, sha TEXT NOT NULL, gid TEXT NOT NULL, PRIMARY KEY(file_id,gid));
CREATE INDEX IF NOT EXISTS media_sha ON media_files(sha);
CREATE TABLE IF NOT EXISTS media_hashes (file_id TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY(file_id,hash));
CREATE INDEX IF NOT EXISTS media_hash_lookup ON media_hashes(hash);
CREATE TABLE IF NOT EXISTS hash_bands (hash TEXT NOT NULL, band INTEGER NOT NULL, value TEXT NOT NULL, PRIMARY KEY(hash,band));
CREATE INDEX IF NOT EXISTS band_lookup ON hash_bands(band,value);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (id BIGINT PRIMARY KEY, menu BIGINT, pending TEXT,
 banned INTEGER NOT NULL DEFAULT 0, permanent INTEGER NOT NULL DEFAULT 0,
 ban_base INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS groups (id TEXT PRIMARY KEY, chat BIGINT UNIQUE NOT NULL,
 title TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'unset', description TEXT NOT NULL DEFAULT '',
 goal INTEGER NOT NULL DEFAULT 5, price INTEGER NOT NULL DEFAULT 0,
 monthly INTEGER NOT NULL DEFAULT 0, locker INTEGER NOT NULL DEFAULT 0,
 threshold INTEGER NOT NULL DEFAULT 100, slot TEXT NOT NULL DEFAULT '22-01',
 weekday INTEGER NOT NULL DEFAULT 2, opened INTEGER NOT NULL DEFAULT 0,
 enabled INTEGER NOT NULL DEFAULT 0, rules TEXT NOT NULL DEFAULT '',
 anti INTEGER NOT NULL DEFAULT 0, last_open DOUBLE PRECISION NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS access (uid BIGINT NOT NULL, gid TEXT NOT NULL,
 source TEXT NOT NULL, expires DOUBLE PRECISION, revoked INTEGER NOT NULL DEFAULT 0,
 base INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(uid,gid));
CREATE TABLE IF NOT EXISTS members (uid BIGINT NOT NULL, gid TEXT NOT NULL,
 present INTEGER NOT NULL DEFAULT 1, joined DOUBLE PRECISION NOT NULL,
 media DOUBLE PRECISION NOT NULL, warned INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(uid,gid));
CREATE TABLE IF NOT EXISTS referrals (invitee BIGINT PRIMARY KEY, inviter BIGINT NOT NULL,
 joined DOUBLE PRECISION NOT NULL, present INTEGER NOT NULL DEFAULT 1,
 valid INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS links (url TEXT PRIMARY KEY, gid TEXT NOT NULL,
 uid BIGINT NOT NULL, purpose TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, uid BIGINT NOT NULL, gid TEXT NOT NULL,
 amount INTEGER NOT NULL, monthly INTEGER NOT NULL, status TEXT NOT NULL,
 created DOUBLE PRECISION NOT NULL, invoice TEXT, charge TEXT UNIQUE);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending ON orders(uid,gid) WHERE status IN ('pending','checkout');
CREATE TABLE IF NOT EXISTS messages (gid TEXT NOT NULL, mid BIGINT NOT NULL,
 uid BIGINT NOT NULL, album TEXT, file_id TEXT, created DOUBLE PRECISION NOT NULL,
 PRIMARY KEY(gid,mid));
CREATE TABLE IF NOT EXISTS notices (gid TEXT NOT NULL, kind TEXT NOT NULL,
 mid BIGINT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(gid,kind));
CREATE TABLE IF NOT EXISTS strikes (uid BIGINT PRIMARY KEY, count INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS reports (gid TEXT NOT NULL, target TEXT NOT NULL,
 reporter BIGINT NOT NULL, PRIMARY KEY(gid,target,reporter));
CREATE TABLE IF NOT EXISTS fingerprints (file_id TEXT PRIMARY KEY, blocked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS seen_media (gid TEXT NOT NULL, file_id TEXT NOT NULL, PRIMARY KEY(gid,file_id));
CREATE TABLE IF NOT EXISTS proof_used (uid BIGINT NOT NULL, gid TEXT NOT NULL, PRIMARY KEY(uid,gid));
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, method TEXT NOT NULL, payload TEXT NOT NULL,
 due DOUBLE PRECISION NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions (day TEXT PRIMARY KEY, gid TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ads (id TEXT PRIMARY KEY, gid TEXT NOT NULL, text TEXT NOT NULL,
 photo TEXT, interval_hours INTEGER NOT NULL DEFAULT 4, enabled INTEGER NOT NULL DEFAULT 1,
 next_at DOUBLE PRECISION NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS processed (id BIGINT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS audit (id TEXT PRIMARY KEY, actor BIGINT NOT NULL,
 action TEXT NOT NULL, created DOUBLE PRECISION NOT NULL);
'''

class DB:
    def __init__(self, url, lock=True):
        self.sqlite = url.startswith('sqlite:///')
        if self.sqlite:
            self.conn = sqlite3.connect(url.removeprefix('sqlite:///'), isolation_level=None)
            self.conn.row_factory = sqlite3.Row
        else:
            import psycopg
            from psycopg.rows import dict_row
            self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
            if lock and not self.one('SELECT pg_try_advisory_lock(731482031) AS locked')['locked']:
                raise RuntimeError('Une autre instance du bot est déjà active.')
        for statement in SCHEMA.split(';'):
            if statement.strip(): self.run(statement)

    def run(self, sql, args=()):
        return self.conn.execute(sql if self.sqlite else sql.replace('?', '%s'), args)

    def one(self, sql, args=()):
        row = self.run(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql, args=()):
        return [dict(row) for row in self.run(sql, args).fetchall()]

    @contextmanager
    def transaction(self):
        self.run('BEGIN')
        try:
            yield
            self.run('COMMIT')
        except BaseException:
            self.run('ROLLBACK')
            raise

    def setting(self, key, default=''):
        row = self.one('SELECT value FROM settings WHERE key=?', (key,))
        return row['value'] if row else default

    def set(self, key, value):
        self.run('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key,str(value)))
