import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from apps.cafeteria.crypto import encrypt_stream
from apps.cafeteria.maintenance import portal_lock


class Command(BaseCommand):
    help = "Offline rotation: rewrite private files under the active media key."

    def handle(self, **options):
        if not settings.DATA_ENCRYPTION_ENABLED:
            raise CommandError("Encrypted storage is required.")
        count = 0
        with portal_lock(exclusive=True):
            root = Path(settings.MEDIA_ROOT)
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if path.is_symlink():
                    raise CommandError("Private media may not contain symlinks.")
                relative = path.relative_to(root).as_posix()
                with default_storage.open(relative) as source:
                    descriptor, temporary = tempfile.mkstemp(prefix=".rotate-", dir=path.parent)
                    try:
                        with os.fdopen(descriptor, "wb") as output:
                            encrypt_stream(source, output, purpose="media", context=relative.encode())
                            output.flush()
                            os.fsync(output.fileno())
                        os.replace(temporary, path)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
                count += 1
        self.stdout.write(f"Re-encrypted {count} private files. Retain old keys while old backups exist.")
