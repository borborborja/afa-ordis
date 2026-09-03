from datetime import datetime
from pathlib import Path
import sqlite3

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Crea una còpia consistent de la base de dades SQLite dins del volum persistent."

    def add_arguments(self, parser):
        parser.add_argument("--output", help="Camí de destinació; per defecte, /data/backups/afa-ordis-AAAAMMDD-HHMMSS.sqlite3")

    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("Aquesta ordre només és disponible amb SQLite.")
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
