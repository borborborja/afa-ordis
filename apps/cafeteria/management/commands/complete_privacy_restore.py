from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.files.storage import default_storage

from apps.cafeteria.database import verify_database
from apps.cafeteria.maintenance import portal_lock
from apps.cafeteria.privacy import finish_restore


class Command(BaseCommand):
    help = "Validate encrypted storage and replay the durable ledger before reopening."

    def add_arguments(self, parser):
        parser.add_argument("--confirm-access-review", action="store_true")

    def handle(self, **options):
        if not settings.DATA_ENCRYPTION_ENABLED or not options["confirm_access_review"]:
            raise CommandError("Encrypted storage and --confirm-access-review are required. Review restored accounts, family links and privileged roles offline before reopening.")
        with portal_lock(exclusive=True):
            verify_database(settings.DATABASES["default"]["NAME"])
            media = Path(settings.MEDIA_ROOT)
            for path in media.rglob("*"):
                if path.is_file():
                    if path.is_symlink():
                        raise CommandError("Symlinks are not allowed in private media.")
                    with default_storage.open(path.relative_to(media).as_posix()) as source:
                        source.read()
            finish_restore(release=True)
        self.stdout.write("Encrypted store and restrictions verified; portal can reopen.")
