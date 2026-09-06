"""Django 5.2 SQLite backend, with explicit SQLCipher DB-API connections/cursors.

No process-wide sqlite3 monkey-patching. SQL adaptation follows Django (BSD).
"""
import datetime
import decimal
from collections.abc import Mapping
from itertools import tee

from django.db.backends.sqlite3.base import DatabaseWrapper as SQLiteWrapper, SQLiteCursorWrapper, decoder
from django.db.backends.sqlite3._functions import register as register_functions
from django.utils.asyncio import async_unsafe
from django.utils.dateparse import parse_date, parse_datetime, parse_time
from django.views.decorators.debug import sensitive_variables
from sqlcipher3 import dbapi2 as Database

from apps.cafeteria.crypto import active_key, keyring

Database.register_converter("bool", b"1".__eq__)
for name, function in (("date", parse_date), ("time", parse_time), ("datetime", parse_datetime), ("timestamp", parse_datetime)):
    Database.register_converter(name, decoder(function))
Database.register_adapter(decimal.Decimal, str)
Database.register_adapter(datetime.date, lambda value: value.isoformat())
Database.register_adapter(datetime.datetime, lambda value: value.isoformat(" "))


@sensitive_variables()
def connect(database, *, key_id=None, **kwargs):
    conn = Database.connect(str(database), **kwargs)
    try:
        key = keyring()[1][key_id] if key_id else active_key("database")[1]
        # Execute directly on DB-API: secret PRAGMAs must never enter query logging.
        conn.execute(f'''PRAGMA key = "x'{key.hex()}'"''')
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA cipher_memory_security = ON")
        if not conn.execute("PRAGMA cipher_version").fetchone():
            raise Database.DatabaseError("SQLCipher unavailable")
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return conn
    except Exception:
        conn.close()
        raise


class CipherCursor(Database.Cursor):
    convert_query = SQLiteCursorWrapper.convert_query

    def execute(self, query, params=None):
        if params is None:
            return super().execute(query)
        names = list(params) if isinstance(params, Mapping) else None
        return super().execute(self.convert_query(query, param_names=names), params)

    def executemany(self, query, params):
        peek, params = tee(iter(params))
        first = next(peek, None)
        names = list(first) if isinstance(first, Mapping) else None
        return super().executemany(self.convert_query(query, param_names=names), params)


class DatabaseWrapper(SQLiteWrapper):
    Database = Database
    display_name = "SQLCipher"

    @async_unsafe
    def get_new_connection(self, conn_params):
        conn = connect(**conn_params)
        register_functions(conn)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA legacy_alter_table = OFF")
        for command in self.init_commands:
            if command.strip():
                conn.execute(command)
        return conn

    def create_cursor(self, name=None):
        return self.connection.cursor(factory=CipherCursor)
