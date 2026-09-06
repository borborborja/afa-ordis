"""Explicit offline conversion; never replaces the running portal or deletes its source."""
import copy
import shutil
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from sqlcipher3 import dbapi2

from apps.cafeteria.backups import build_encrypted_backup
from apps.cafeteria.crypto import EncryptedStorage
from apps.cafeteria.database import export_to_active_key
from apps.cafeteria.views import _extract_portal_backup


class Command(BaseCommand):
    help = "Convert a trusted v1 ZIP/plain SQLite backup into a migrated encrypted v2 backup. The source is preserved."

    def add_arguments(self, parser):
        parser.add_argument("--input", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--confirm-legacy-import", action="store_true")

    def handle(self, **options):
        if not settings.DATA_ENCRYPTION_ENABLED or not options["confirm_legacy_import"]:
            raise CommandError("Explicit legacy-import confirmation and encrypted configuration are required.")
        source_path = Path(options["input"])
        output = Path(options["output"])
        if not source_path.is_file() or source_path.stat().st_size > 100 * 1024 * 1024 or output.exists():
            raise CommandError("Invalid input, excessive size or output already exists.")
        extracted = None
        alias = "privacy_import"
        try:
            with tempfile.TemporaryDirectory(dir=settings.PRIVATE_TEMP_DIR) as directory:
                work = Path(directory)
                source_db = source_path
                if zipfile.is_zipfile(source_path):
                    with zipfile.ZipFile(source_path) as legacy:
                        if sum(item.file_size for item in legacy.infolist()) > 100 * 1024 * 1024:
                            raise CommandError("Legacy archive exceeds the 100 MiB conversion limit.")
                    extracted, source_db = _extract_portal_backup(source_path)
                raw = dbapi2.connect(f"file:{source_db}?mode=ro", uri=True)
                try:
                    raw.execute("PRAGMA temp_store=MEMORY")
                    raw.execute("PRAGMA cipher_memory_security=ON")
                    if raw.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                        raise CommandError("Legacy database failed integrity validation.")
                    tables = {row[0] for row in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if not {"django_migrations", "auth_user", "cafeteria_student"}.issubset(tables):
                        raise CommandError("Input is not a recognized portal database.")
                    export_to_active_key(raw, work / "converted.sqlite3")
                finally:
                    raw.close()
                db_settings = copy.deepcopy(connections["default"].settings_dict)
                db_settings["NAME"] = str(work / "converted.sqlite3")
                connections.databases[alias] = db_settings
                call_command("migrate", database=alias, interactive=False, verbosity=0)
                storage = EncryptedStorage(location=work / "media")
                if extracted:
                    from django.core.files import File
                    for file in (extracted / "media").rglob("*"):
                        if file.is_file():
                            with file.open("rb") as entry:
                                storage.save(file.relative_to(extracted / "media").as_posix(), File(entry))
                with build_encrypted_backup(database_connection=connections[alias], media_root=work / "media", restrictions=[]) as backup, output.open("xb") as target:
                    output.chmod(0o600)
                    shutil.copyfileobj(backup, target)
                connections[alias].close()
        finally:
            if alias in connections:
                connections[alias].close()
                del connections[alias]
                connections.databases.pop(alias, None)
            if extracted:
                shutil.rmtree(extracted)
        self.stdout.write("Legacy backup converted; source untouched. Review consent and restore with the latest external restriction ledger.")
