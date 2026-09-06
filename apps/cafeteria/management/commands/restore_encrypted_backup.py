import json
import shutil
from pathlib import Path

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError

from apps.cafeteria.backups import extract_encrypted_backup
from apps.cafeteria.maintenance import portal_lock
from apps.cafeteria.privacy import (
    finish_restore, load_restriction_ledger, mark_restore_pending, merge_ledgers,
    read_external_ledger, save_restriction_ledger,
)
from apps.cafeteria.views import _restore_portal_state, _validate_restore_database


class Command(BaseCommand):
    help = "Offline disaster recovery using an encrypted backup and the latest external restriction ledger."

    def add_arguments(self, parser):
        parser.add_argument("--input", required=True)
        parser.add_argument("--ledger", required=True)
        parser.add_argument("--confirm-latest-ledger", action="store_true")
        parser.add_argument("--confirm-replace", action="store_true")

    def handle(self, **options):
        if not settings.DATA_ENCRYPTION_ENABLED or not options["confirm_latest_ledger"] or not options["confirm_replace"]:
            raise CommandError("Encrypted storage and both confirmations are required. Stop the app and save a recovery copy first.")
        if Path(options["input"]).stat().st_size > 100 * 1024 * 1024:
            raise CommandError("Backup exceeds the 100 MiB limit.")
        with portal_lock(exclusive=True):
            with open(options["ledger"], "rb") as source:
                ledger = merge_ledgers(load_restriction_ledger(), read_external_ledger(source))
            with open(options["input"], "rb") as source:
                staging, database, manifest = extract_encrypted_backup(source)
            try:
                ledger = merge_ledgers(ledger, json.loads((staging / "restrictions.json").read_text()))
                _validate_restore_database(database, key_id=manifest["database_key"])
                mark_restore_pending()
                save_restriction_ledger(ledger)
                _restore_portal_state(database, staging, key_id=manifest["database_key"])
                Session.objects.all().delete()
                finish_restore()
            finally:
                shutil.rmtree(staging)
        self.stdout.write("Encrypted backup restored and restrictions reapplied. Sessions revoked. Portal stays closed until access review and complete_privacy_restore --confirm-access-review.")
