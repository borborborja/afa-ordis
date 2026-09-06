"""One entry point for every database snapshot/restore connection."""
from django.conf import settings
from django.db import connection
from django.views.decorators.debug import sensitive_variables

if settings.DATABASE_ENGINE == "config.sqlcipher":
    from config.sqlcipher.base import Database as dbapi, connect
else:
    import sqlite3 as dbapi

    def connect(database, *, key_id=None, **kwargs):
        return dbapi.connect(str(database), **kwargs)


def snapshot_database(path, database_connection=None):
    source = database_connection if database_connection is not None else connection
    source.ensure_connection()
    target = connect(path)
    try:
        source.connection.backup(target)
    finally:
        target.close()


@sensitive_variables()
def export_to_active_key(source, path):
    """SQLCipher backup() cannot cross encryption keys; use the supported export API."""
    import os
    from .crypto import active_key
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    key = active_key("database")[1]
    source.execute(f'''ATTACH DATABASE ? AS afa_export KEY "x'{key.hex()}'"''', (str(path),))
    try:
        source.execute("SELECT sqlcipher_export('afa_export')").fetchone()
        version = source.execute("PRAGMA user_version").fetchone()[0]
        source.execute(f"PRAGMA afa_export.user_version = {int(version)}")
    finally:
        source.execute("DETACH DATABASE afa_export")


def verify_database(path, key_id=None):
    source = connect(f"file:{path}?mode=ro", uri=True, key_id=key_id)
    try:
        if settings.DATA_ENCRYPTION_ENABLED and source.execute("PRAGMA cipher_integrity_check").fetchall():
            raise ValueError("Encrypted database authentication failed")
        if source.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("Database integrity check failed")
    finally:
        source.close()
