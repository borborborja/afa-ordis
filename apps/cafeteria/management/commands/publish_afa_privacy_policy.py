"""Publish the AFA-approved policy only after real-world confirmations."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.cafeteria.approved_privacy_policy import (
    CONTROLLER,
    HEALTH_TEXT_CA,
    HEALTH_TEXT_ES,
    POLICY_VERSION,
    RETENTION_SCHEDULE,
    TEXT_CA,
    TEXT_ES,
)
from apps.cafeteria.models import PrivacyNotice, RetentionRule, Role, log_event, user_has_role


class Command(BaseCommand):
    help = "Publish the approved AFA policy and retention rules after documented real-world verification."

    def add_arguments(self, parser):
        parser.add_argument("--approved-by", required=True, help="Active account username or email of the authorised approver")
        parser.add_argument("--confirm-policy-approved-by-afa", action="store_true")
        parser.add_argument("--confirm-retention-approved", action="store_true")
        parser.add_argument("--confirm-processor-contracts", action="store_true")
        parser.add_argument("--confirm-impact-assessment", action="store_true")
        parser.add_argument("--confirm-key-recovery", action="store_true")

    def handle(self, **options):
        confirmations = (
            "confirm_policy_approved_by_afa",
            "confirm_retention_approved",
            "confirm_processor_contracts",
            "confirm_impact_assessment",
            "confirm_key_recovery",
        )
        if not all(options[name] for name in confirmations):
            raise CommandError(
                "This command requires all five confirmations: AFA policy approval, approved retention, "
                "processor contracts/transfers, impact assessment/legal bases, and tested key recovery."
            )

        identifier = options["approved_by"].strip()
        user_model = get_user_model()
        approvers = user_model.objects.filter(is_active=True).filter(
            Q(username=identifier) | Q(email__iexact=identifier)
        ).distinct()
        if approvers.count() != 1:
            raise CommandError("Provide the username or email of exactly one active authorised account.")
        approver = approvers.get()
        if not user_has_role(approver, Role.ADMIN):
            raise CommandError("The approver must be an administrator.")

        with transaction.atomic():
            if PrivacyNotice.current():
                raise CommandError("A published privacy notice already exists. Published versions are immutable; use the privacy administration screen to publish a reviewed new version.")
            if PrivacyNotice.objects.filter(version=POLICY_VERSION).exists():
                raise CommandError(f"The unpublished version {POLICY_VERSION} already exists. Review or remove it through the authorised privacy process; it will not be overwritten.")
            if RetentionRule.objects.exists():
                raise CommandError("Retention rules already exist. They will not be overwritten; review them in Privacy Administration before publishing a notice.")

            for item in RETENTION_SCHEDULE:
                rule = RetentionRule.objects.create(
                    category=item.category,
                    days=item.days,
                    justification=item.justification,
                    approved_by=approver,
                )
                log_event(approver, "privacy.retention_approved", rule, {"category": rule.category})

            notice = PrivacyNotice.objects.create(
                version=POLICY_VERSION,
                **CONTROLLER,
                text_ca=TEXT_CA,
                text_es=TEXT_ES,
                health_text_ca=HEALTH_TEXT_CA,
                health_text_es=HEALTH_TEXT_ES,
                contracts_verified=True,
                assessment_approved=True,
                recovery_verified=True,
                approved_by=approver,
                published_at=timezone.now(),
            )
            log_event(approver, "privacy.notice_published", notice, {"version": notice.version})

        self.stdout.write(self.style.SUCCESS(
            f"Published privacy notice {POLICY_VERSION} with all retention rules and its internal audit record."
        ))
