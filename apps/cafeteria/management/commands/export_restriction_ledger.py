import io
import json
import os

from django.core.management.base import BaseCommand

from apps.cafeteria.crypto import encrypt_stream
from apps.cafeteria.maintenance import portal_lock
from apps.cafeteria.privacy import load_restriction_ledger


class Command(BaseCommand):
    help = "Export the current encrypted restriction ledger for separate external custody."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True)

    def handle(self, **options):
        with portal_lock(exclusive=True, name="privacy-ledger"):
            source = io.BytesIO(json.dumps(load_restriction_ledger()).encode())
            fd = os.open(options["output"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as target:
                encrypt_stream(source, target, purpose="backup", context=b"restriction-ledger")
                target.flush()
                os.fsync(target.fileno())
        self.stdout.write("Encrypted restriction ledger exported. Keep the latest version outside the VPS.")
