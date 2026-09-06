"""Financial retention needs a documented closure and a no-litigation decision."""
import json
import uuid
from datetime import datetime, time, timedelta
from types import SimpleNamespace

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.cafeteria.maintenance import portal_lock
from apps.cafeteria.privacy import journal_restriction, purge_closed_accounting, retention_days


class Command(BaseCommand):
    help = "Preview financial minimization; apply only after approved retention, fiscal closure and litigation review."

    def add_arguments(self, parser):
        parser.add_argument("--closed-through", required=True, type=lambda value: datetime.strptime(value, "%Y-%m-%d").date())
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm-no-legal-hold", action="store_true")

    def handle(self, **options):
        cutoff = min(timezone.now() - timedelta(days=retention_days("accounting")),
                     timezone.make_aware(datetime.combine(options["closed_through"], time.min)))
        with portal_lock(exclusive=True), transaction.atomic():
            counts = purge_closed_accounting(cutoff, dry_run=True)
            self.stdout.write(json.dumps({"cutoff": cutoff.isoformat(), "candidates": counts}))
            if options["apply"]:
                if not options["confirm_no_legal_hold"]:
                    raise CommandError("Obtain documented financial closure and confirmation that no legal preservation duty applies.")
                journal_restriction(SimpleNamespace(privacy_id=uuid.uuid4()), category="accounting", destroy_after=cutoff)
                self.stdout.write("Applied. Export and separately retain the updated restriction ledger now.")
