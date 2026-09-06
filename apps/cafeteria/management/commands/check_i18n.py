from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.cafeteria.i18n_audit import audit_project


class Command(BaseCommand):
    help = "Bloqueja textos visibles sense traduir i catàlegs d'idioma incomplets."

    def handle(self, *args, **options):
        errors = audit_project(Path(settings.BASE_DIR))
        if errors:
            raise CommandError("\n".join(errors))
        self.stdout.write(self.style.SUCCESS("La qualitat lingüística del portal és correcta."))
