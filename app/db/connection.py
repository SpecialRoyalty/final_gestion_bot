import sqlite3
from contextlib import contextmanager

class DB:
    def __init__(self, url, lock=True, initialize=True):
        self.sqlite = url.startswith('sqlite:///')
        if self.sqlite:
            self.conn = sqlite3.connect(url.removeprefix('sqlite:///'), isolation_level=None)
            self.conn.row_factory = sqlite3.Row
        else:
            import psycopg
            from psycopg.rows import dict_row
            self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
            if lock and not self.one('SELECT pg_try_advisory_lock(731482031) AS locked')['locked']:
                self.conn.close()
                raise RuntimeError('Une autre instance du bot est déjà active.')
        if initialize:
            try:
                from .migration import migrate
                migrate(self)
            except BaseException:
                self.conn.close()
                raise

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
