import secrets

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.cafeteria.models import RecoveryCode, log_event


class Command(BaseCommand):
    help = "Offline break-glass recovery after identity verification; invalidates password, sessions and old MFA factors."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, required=True)
        parser.add_argument("--confirm-identity-verified", action="store_true")

    @transaction.atomic
    def handle(self, **options):
        if not options["confirm_identity_verified"]:
            raise CommandError("Verify identity through an independent channel before resetting MFA.")
        user = User.objects.filter(pk=options["user_id"], is_active=True).first()
        if not user:
            raise CommandError("Active account not found.")
        user.set_password(secrets.token_urlsafe(64))
        user.save(update_fields=["password"])
        TOTPDevice.objects.filter(user=user).delete()
        RecoveryCode.objects.filter(user=user).delete()
        log_event(None, "security.mfa_offline_reset", user)
        self.stdout.write("Old password, sessions and factors invalidated. The verified person must reset their password and enroll a new factor. No email was sent.")
