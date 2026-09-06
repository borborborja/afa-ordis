"""Coordinate web threads and the scheduler while replacing SQLite and private files."""
from contextlib import contextmanager
import fcntl
from pathlib import Path

from django.conf import settings
from django.db import connection


class PortalBusy(Exception):
    pass


@contextmanager
def portal_lock(*, exclusive=False, name="maintenance"):
    database_name = str(connection.settings_dict["NAME"])
    explicit_path = getattr(settings, "PORTAL_LOCK_PATH", None)
    if not explicit_path and (database_name == ":memory:" or "mode=memory" in database_name):
        yield  # Django's isolated in-memory test database has no external users.
        return
    path = Path(explicit_path) if explicit_path else Path(database_name).parent / ".afa-ordis.lock"
    if name != "maintenance":
        path = path.with_name(f"{path.name}.{name}")
    with path.open("a+b") as lock_file:
        try:
            fcntl.flock(lock_file, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise PortalBusy from error
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
