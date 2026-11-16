"""Deployment smoke check, no Telegram credentials or production database required."""
from app.db import DB
from app.db.migration import EXPECTED, MIGRATIONS, columns, inspect, migrate


def main():
    # Import the real entry point, to catch missing modules at image build time.
    from app import main as entrypoint
    if not callable(entrypoint.main): raise RuntimeError('Point d’entrée invalide')
    if len(MIGRATIONS)!=3 or not EXPECTED: raise RuntimeError('Schéma embarqué incomplet')
    db=DB('sqlite:///:memory:')
    try:
        migrate(db)
        missing=[table for table in EXPECTED if not columns(db,table)]
        if missing or inspect(db): raise RuntimeError('Initialisation du schéma incomplète')
        actual=[r['version'] for r in db.all('SELECT version FROM network_schema_migrations ORDER BY version')]
        if actual != [version for version,_ in MIGRATIONS]: raise RuntimeError('Migrations incomplètes')
    finally:
        db.conn.close()
    print('OK — imports, schéma embarqué et migrations vérifiés ; aucune connexion externe.')

if __name__=='__main__': main()
