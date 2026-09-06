import base64
import json
import os
import secrets
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Generate a private key file; optionally retain old keys for rotation. Never prints secrets."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True)
        parser.add_argument("--extend", help="Existing key file whose old keys must remain available")

    def handle(self, **options):
        ring = {"version": 1, "keys": {}, "active": {}}
        if options["extend"]:
            from apps.cafeteria.crypto import keyring
            _, old = keyring(options["extend"])
            ring["keys"] = {kid: base64.b64encode(key).decode() for kid, key in old.items()}
        for purpose in ("database", "media", "backup"):
            kid = secrets.token_hex(16)
            ring["active"][purpose] = kid
            ring["keys"][kid] = base64.b64encode(secrets.token_bytes(32)).decode()
        try:
            path = Path(options["output"])
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as target:
                json.dump(ring, target)
                target.flush()
                os.fsync(target.fileno())
        except OSError as error:
            raise CommandError("Cannot create key file; existing files are never overwritten.") from error
        self.stdout.write("Key file created. Keep a recovery copy separately from data backups.")
