from datetime import datetime
from pathlib import Path
from django.conf import settings

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Create a complete encrypted portal backup (plaintext DB-only support is development-only)."

    def add_arguments(self, parser):
        parser.add_argument("--output", help="Private destination for the complete .afaenc backup")

    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("Aquesta ordre només és disponible amb SQLite.")
        if settings.DATA_ENCRYPTION_ENABLED:
            from apps.cafeteria.backups import build_encrypted_backup
            from apps.cafeteria.maintenance import portal_lock
            import shutil
            output = Path(options["output"] or f"/data/backups/afa-ordis-{datetime.now():%Y%m%d-%H%M%S}.afaenc")
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with portal_lock(exclusive=True), build_encrypted_backup() as source, output.open("xb") as target:
                output.chmod(0o600)
                shutil.copyfileobj(source, target)
                import os
                target.flush()
                os.fsync(target.fileno())
            from datetime import timedelta
            from django.utils import timezone
            from apps.cafeteria.models import BackupCustody
            copy = BackupCustody.objects.create(expires_at=timezone.now() + timedelta(days=settings.BACKUP_RETENTION_DAYS))
            self.stdout.write(str(output))
            self.stdout.write(f"Custody reference: {copy.pk}. Confirm separate external storage in the portal; generation alone is not custody.")
            return
        import sqlite3
        default_path = Path("/data/backups") / f"afa-ordis-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
        output = Path(options["output"] or default_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        connection.ensure_connection()
        destination = sqlite3.connect(output)
        try:
            connection.connection.backup(destination)
        finally:
            destination.close()
        self.stdout.write(self.style.SUCCESS(str(output)))
