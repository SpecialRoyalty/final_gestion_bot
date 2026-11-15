"""PostgreSQL in production; SQLite solely for isolated offline tests."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

class DB:
    def __init__(self, url):
        self.pg = not url.startswith('sqlite:')
        if self.pg:
            import psycopg
            from psycopg.rows import dict_row
            self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
        else:
            self.conn = sqlite3.connect(url[7:], isolation_level=None)
            self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON') if not self.pg else None

    def run(self, sql, args=()):
        return self.conn.execute(sql.replace('?', '%s') if self.pg else sql, args)

    def one(self, sql, args=()):
        row = self.run(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql, args=()):
        return [dict(r) for r in self.run(sql, args).fetchall()]

    @contextmanager
    def transaction(self):
        self.run('BEGIN')
        try:
            yield
            self.run('COMMIT')
        except BaseException:
            self.run('ROLLBACK')
            raise

    def init(self):
        for statement in Path(__file__).with_name('schema.sql').read_text().split(';'):
            if statement.strip():
                self.run(statement)

    def setting(self, key, default=None):
        r = self.one('SELECT value FROM settings WHERE key=?', (key,))
        return json.loads(r['value']) if r else default

    def set(self, key, value):
        self.run('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key,json.dumps(value,ensure_ascii=False)))
