"""Read-only schema report. Never prints database URL, credentials or row values."""
import json
import os
from .connection import DB
from .migration import EXPECTED, columns, inspect


def main():
    db=DB(os.environ['DATABASE_URL'],lock=False,initialize=False)
    try:
        print(json.dumps({'engine':'sqlite' if db.sqlite else 'postgresql',
            'schema': 'main' if db.sqlite else db.one('SELECT current_schema() AS name')['name'],
            'incompatibilities':inspect(db),
            'tables':{table:columns(db,table) for table in EXPECTED}},ensure_ascii=False,indent=2))
    finally: db.conn.close()

if __name__=='__main__': main()
