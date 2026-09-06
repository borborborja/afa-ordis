import uuid
from types import SimpleNamespace

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.cafeteria.maintenance import portal_lock
from apps.cafeteria.models import AuditEvent
from apps.cafeteria.privacy import journal_restriction


class Command(BaseCommand):
    help = "Preserve/release a subject's reserved evidence, with an external durable ledger and case reference."

    def add_arguments(self, parser):
        parser.add_argument("--subject", required=True, type=uuid.UUID)
        parser.add_argument("--case", required=True)
        parser.add_argument("--release", action="store_true")
        parser.add_argument("--confirm-authority", action="store_true")

    def handle(self, **options):
        import re
        if not options["confirm_authority"] or not re.fullmatch(r"[A-Za-z0-9/-]{3,80}", options["case"]):
            raise CommandError("A valid case reference and explicit confirmation of legal authority are required.")
        with portal_lock(exclusive=True), transaction.atomic():
            journal_restriction(SimpleNamespace(privacy_id=options["subject"]),
                category="release_hold" if options["release"] else "legal_hold", destroy_after=timezone.now())
            AuditEvent.objects.create(action="privacy.legal_hold_changed", target_type="subject", target_id=str(options["subject"]),
                details={"case_reference": options["case"], "enabled": not options["release"]})
        self.stdout.write("Legal hold updated. Export and separately retain the updated restriction ledger now.")
