"""Privacy regressions use synthetic records and disposable encryption keys only."""
import base64
import io
import json
import secrets
import sqlite3
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from .crypto import EncryptedStorage, decrypt_stream, encrypt_stream, keyring
from .models import (
    AcademicYear, BlockedData, ConsentRecord, CourseGroup, DataRequest,
    DailyReportRecipient, Diet, Family, FamilyMembership, MealBooking, MealSettings,
    PrivacyNotice, RecoveryCode, RetentionRule, Role, ServiceDay, Student,
)
from .privacy import load_restriction_ledger, privacy_ready, replay_restrictions, restrict_student


def test_key_file(directory, *, old=None):
    ring = {"version": 1, "active": {}, "keys": dict(old["keys"]) if old else {}}
    for purpose in ("database", "media", "backup"):
        kid = secrets.token_hex(16)
        ring["active"][purpose] = kid
        ring["keys"][kid] = base64.b64encode(secrets.token_bytes(32)).decode()
    target = Path(directory) / f"keys-{secrets.token_hex(4)}.json"
    target.write_text(json.dumps(ring))
    target.chmod(0o600)
    return str(target), ring


class CryptoTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.key_file, self.ring = test_key_file(self.temp.name)
        self.override = override_settings(ENCRYPTION_KEY_FILE=self.key_file, PRIVATE_TEMP_DIR=self.temp.name, MEDIA_ROOT=self.temp.name)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def encrypted(self, data=b"Synthetic clinical data", context=b"example"):
        output = io.BytesIO()
        encrypt_stream(io.BytesIO(data), output, purpose="media", context=context)
        return output.getvalue()

    def test_roundtrip_empty_and_multiple_chunks(self):
        for data in (b"", b"Synthetic clinical data" * 9000):
            encrypted = self.encrypted(data)
            self.assertNotIn(b"Synthetic clinical data", encrypted)
            output = io.BytesIO()
            decrypt_stream(io.BytesIO(encrypted), output, purpose="media", context=b"example")
            self.assertEqual(output.getvalue(), data)

    def test_truncation_tampering_wrong_context_and_wrong_purpose_rejected(self):
        payload = self.encrypted()
        for altered in (payload[:-1], payload[:40], payload + b"extra", payload[:-20] + bytes([payload[-20] ^ 1]) + payload[-19:]):
            with self.assertRaises(ValueError):
                decrypt_stream(io.BytesIO(altered), io.BytesIO(), purpose="media", context=b"example")
        for purpose, context in (("backup", b"example"), ("media", b"swapped-path")):
            with self.assertRaises(ValueError):
                decrypt_stream(io.BytesIO(payload), io.BytesIO(), purpose=purpose, context=context)

    def test_missing_wrong_or_shared_keys_fail_closed(self):
        with override_settings(ENCRYPTION_KEY_FILE="/nonexistent/keys"):
            with self.assertRaises(ImproperlyConfigured):
                keyring()
        payload = self.encrypted()
        other, _ = test_key_file(self.temp.name)
        with override_settings(ENCRYPTION_KEY_FILE=other):
            with self.assertRaises(ValueError):
                decrypt_stream(io.BytesIO(payload), io.BytesIO(), purpose="media", context=b"example")

    def test_rotation_retains_old_file_readability(self):
        old = self.encrypted()
        new, _ = test_key_file(self.temp.name, old=self.ring)
        with override_settings(ENCRYPTION_KEY_FILE=new):
            restored = io.BytesIO()
            decrypt_stream(io.BytesIO(old), restored, purpose="media", context=b"example")
            self.assertEqual(restored.getvalue(), b"Synthetic clinical data")
            self.assertNotEqual(old, self.encrypted())

    def test_storage_never_exposes_url_or_unauthenticated_content(self):
        storage = EncryptedStorage(location=self.temp.name)
        name = storage.save("alergies/synthetic.pdf", ContentFile(b"%PDF medical-marker"))
        self.assertNotIn(b"medical-marker", Path(storage.path(name)).read_bytes())
        self.assertEqual(storage.open(name).read(), b"%PDF medical-marker")
        with self.assertRaises(ValueError):
            storage.url(name)
        data = Path(storage.path(name)).read_bytes()
        Path(storage.path(name)).write_bytes(data[:-1])
        with self.assertRaises(ValueError):
            storage.open(name)

    def test_sqlcipher_database_and_wal_are_not_readable_as_sqlite(self):
        from config.sqlcipher.base import connect
        path = Path(self.temp.name) / "isolated.sqlite3"
        conn = connect(path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE synthetic(value TEXT)")
            conn.execute("INSERT INTO synthetic VALUES (?)", ("clinical-marker-8273",))
            conn.commit()
            for file in Path(self.temp.name).glob("isolated.sqlite3*"):
                self.assertNotIn(b"clinical-marker-8273", file.read_bytes())
            with sqlite3.connect(path) as plain:
                with self.assertRaises(sqlite3.DatabaseError):
                    plain.execute("SELECT * FROM synthetic").fetchall()
        finally:
            conn.close()
        other, _ = test_key_file(self.temp.name)
        with override_settings(ENCRYPTION_KEY_FILE=other):
            from sqlcipher3.dbapi2 import DatabaseError
            with self.assertRaises(DatabaseError):
                connect(path)

    def test_database_export_changes_key_and_keeps_source_unchanged(self):
        from config.sqlcipher.base import connect
        from .database import export_to_active_key
        old_path = Path(self.temp.name) / "old.sqlite3"
        new_path = Path(self.temp.name) / "new.sqlite3"
        source = connect(old_path)
        source.execute("CREATE TABLE synthetic (value TEXT)")
        source.execute("INSERT INTO synthetic VALUES ('synthetic-rotation')")
        source.commit()
        new, _ = test_key_file(self.temp.name, old=self.ring)
        try:
            with override_settings(ENCRYPTION_KEY_FILE=new):
                export_to_active_key(source, new_path)
                target = connect(new_path)
                try:
                    self.assertEqual(target.execute("SELECT value FROM synthetic").fetchone()[0], "synthetic-rotation")
                finally:
                    target.close()
            self.assertEqual(source.execute("SELECT value FROM synthetic").fetchone()[0], "synthetic-rotation")
        finally:
            source.close()


@override_settings(PRIVACY_ENFORCED=True)
class PrivacyPublicationCommandTests(TestCase):
    def setUp(self):
        self.approver = User.objects.create_superuser(
            "privacy.officer", "privacy.officer@example.test", "Synthetic-long-password-6723",
        )

    def command_args(self):
        return (
            "publish_afa_privacy_policy", "--approved-by", self.approver.email,
            "--confirm-policy-approved-by-afa", "--confirm-retention-approved",
            "--confirm-processor-contracts", "--confirm-impact-assessment", "--confirm-key-recovery",
        )

    def test_requires_all_real_world_confirmations(self):
        with self.assertRaises(CommandError):
            call_command("publish_afa_privacy_policy", "--approved-by", self.approver.email)
        self.assertFalse(PrivacyNotice.objects.exists())
        self.assertFalse(RetentionRule.objects.exists())

    def test_requires_a_privacy_authorised_approver(self):
        unapproved = User.objects.create_user(
            "ordinary.account", "ordinary.account@example.test", "Synthetic-long-password-6723",
        )
        args = list(self.command_args())
        args[2] = unapproved.email
        with self.assertRaises(CommandError):
            call_command(*args)
        self.assertFalse(PrivacyNotice.objects.exists())

    def test_publishes_approved_text_and_creates_internal_audit_record(self):
        call_command(*self.command_args())
        notice = PrivacyNotice.current()
        self.assertIsNotNone(notice)
        self.assertEqual(notice.controller, "AFA Escola Maria Pagès i Trayter")
        self.assertEqual(notice.contact_email, "privacitat@afaescolaordis.org")
        self.assertNotIn("[PENDENT", notice.text_ca)
        self.assertEqual(set(RetentionRule.objects.values_list("category", flat=True)), set(RetentionRule.Category.values))
        self.assertTrue(privacy_ready())

    def test_never_overwrites_a_published_notice_or_retention_rules(self):
        call_command(*self.command_args())
        with self.assertRaises(CommandError):
            call_command(*self.command_args())
        self.assertEqual(PrivacyNotice.objects.count(), 1)
        self.assertEqual(RetentionRule.objects.count(), len(RetentionRule.Category.values))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", MFA_REQUIRED=False, PRIVACY_ENFORCED=False)
class PrivacyFlows(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.key_file, _ = test_key_file(self.temp.name)
        self.override = override_settings(ENCRYPTION_KEY_FILE=self.key_file, PRIVATE_TEMP_DIR=self.temp.name,
            MEDIA_ROOT=self.temp.name + "/media", PRIVACY_LEDGER_PATH=self.temp.name + "/ledger.afaenc",
            STORAGES={"default": {"BACKEND": "apps.cafeteria.crypto.EncryptedStorage"}, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.today = timezone.localdate()
        self.year = AcademicYear.objects.create(name="Synthetic year", starts_on=self.today - timedelta(days=5), ends_on=self.today + timedelta(days=365), is_active=True)
        self.group = CourseGroup.objects.create(academic_year=self.year, name="I4")
        self.diet = Diet.objects.create(name="Ordinària")
        self.family = Family.objects.create(name="Synthetic family", phone="600000000")
        self.tutor = User.objects.create_user("parent@example.test", "parent@example.test", "Synthetic-long-password-6723")
        self.other = User.objects.create_user("other@example.test", "other@example.test", "Synthetic-long-password-6723")
        self.admin = User.objects.create_superuser("admin@example.test", "admin@example.test", "Synthetic-long-password-6723")
        self.cook = User.objects.create_user("cook@example.test", "cook@example.test", "Synthetic-long-password-6723")
        self.reviewer = User.objects.create_user("reviewer@example.test", "reviewer@example.test", "Synthetic-long-password-6723")
        self.cook.groups.add(Group.objects.get_or_create(name=Role.KITCHEN)[0])
        self.reviewer.groups.add(Group.objects.get_or_create(name=Role.HEALTH_REVIEWER)[0], Group.objects.get_or_create(name=Role.PRIVACY)[0])
        FamilyMembership.objects.create(family=self.family, user=self.tutor)
        self.student = Student.objects.create(family=self.family, default_diet=self.diet, course_group=self.group,
            first_name="SyntheticChild", last_name="Example", has_allergy=True, allergy_title="PRIVATE DIAGNOSIS",
            allergy_details="PRIVATE CLINICAL HISTORY", kitchen_instructions="Avoid eggs", allergy_review_status="pending")
        self.student.allergy_document.save("medical.pdf", ContentFile(b"%PDF synthetic-medical-marker"), save=True)
        ServiceDay.objects.create(academic_year=self.year, date=self.today, is_service_day=True)
        MealBooking.objects.create(student=self.student, date=self.today, diet=self.diet)
        for category in RetentionRule.Category.values:
            RetentionRule.objects.create(category=category, days=30, justification="Synthetic test policy", approved_by=self.reviewer)

    def publish(self):
        return PrivacyNotice.objects.create(version="v1", controller="Synthetic AFA", tax_id="TEST", address="Synthetic", contact_email="privacy@example.test",
            text_ca="Synthetic reviewed policy " * 10, text_es="Synthetic reviewed policy " * 10,
            health_text_ca="Synthetic reviewed health consent " * 10, health_text_es="Synthetic reviewed health consent " * 10,
            contracts_verified=True, assessment_approved=True, recovery_verified=True, published_at=timezone.now())

    def test_medical_document_requires_explicit_permission_not_admin_status(self):
        for user, expected in ((self.admin, 403), (self.cook, 403), (self.other, 403), (self.reviewer, 200), (self.tutor, 200)):
            self.client.force_login(user)
            response = self.client.get(reverse("cafeteria:allergy_document_download", args=[self.student.pk]))
            self.assertEqual(response.status_code, expected)

    def test_clinical_forms_hidden_from_ordinary_admin(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("cafeteria:management_student_edit", args=[self.student.pk]))
        self.assertNotContains(response, "PRIVATE CLINICAL HISTORY")
        self.assertNotContains(response, 'name="allergy_document"')

    def test_kitchen_has_only_today_operational_information(self):
        self.client.force_login(self.cook)
        response = self.client.get(reverse("cafeteria:kitchen_report") + "?date=2000-01-01")
        self.assertContains(response, "Avoid eggs")
        for private in ("PRIVATE DIAGNOSIS", "PRIVATE CLINICAL HISTORY", "parent@example.test", "600000000"):
            self.assertNotContains(response, private)
        self.assertEqual(self.client.get(reverse("cafeteria:people")).status_code, 403)
        self.assertEqual(self.client.get(reverse("cafeteria:daily_reports")).status_code, 403)

    def test_daily_email_is_an_individual_notice_without_personal_data(self):
        from .tasks import send_daily_report
        meal_settings = MealSettings.objects.create(academic_year=self.year)
        for account in (self.cook, self.admin, self.other):
            DailyReportRecipient.objects.create(settings=meal_settings, email=account.email, user=account)
        self.assertTrue(send_daily_report(self.today.isoformat()))
        self.assertEqual(len(mail.outbox), 2)
        for message in mail.outbox:
            self.assertEqual(len(message.to), 1)
            for private in ("SyntheticChild", "Avoid eggs", "PRIVATE", "600000000"):
                self.assertNotIn(private, message.body)

    @override_settings(PRIVACY_ENFORCED=True)
    def test_collection_is_not_blocked_by_an_internal_policy_record(self):
        self.client.force_login(self.admin)
        self.assertFalse(privacy_ready())
        self.assertNotEqual(self.client.post(reverse("cafeteria:invitation_create"), {}).status_code, 503)
        self.publish()
        self.assertTrue(privacy_ready())

    @override_settings(PRIVACY_ENFORCED=True)
    def test_public_policy_and_generic_notification_work_before_internal_publication(self):
        from .tasks import send_daily_report
        response = self.client.get(reverse("cafeteria:privacy_notice"))
        self.assertContains(response, "AFA Escola Maria Pagès i Trayter")
        self.assertNotContains(response, "pendent de validació")
        meal_settings = MealSettings.objects.create(academic_year=self.year)
        DailyReportRecipient.objects.create(settings=meal_settings, email=self.cook.email, user=self.cook)
        self.assertTrue(send_daily_report(self.today.isoformat()))

    def test_published_notices_are_immutable(self):
        from django.core.exceptions import ValidationError
        notice = self.publish()
        notice.text_ca = "Changed"
        with self.assertRaises(ValidationError):
            notice.save()

    def test_restriction_archives_health_preserves_safety_and_survives_replay(self):
        notice = self.publish()
        ConsentRecord.objects.create(student=self.student, representative=self.tutor, notice=notice, authority_confirmed=True)
        with self.captureOnCommitCallbacks(execute=True):
            restrict_student(self.student, actor=self.tutor)
        self.student.refresh_from_db()
        self.assertTrue(self.student.safety_hold)
        self.assertIsNone(self.student.has_allergy)
        self.assertFalse(self.student.allergy_document)
        self.assertEqual(self.student.allergy_details, "")
        self.assertTrue(BlockedData.objects.filter(subject=self.student.privacy_id).exists())
        self.assertFalse(ConsentRecord.objects.filter(withdrawn_at__isnull=True).exists())
        ledger = load_restriction_ledger()
        replay_restrictions(ledger)
        self.assertEqual(BlockedData.objects.count(), 1)
        self.assertNotIn(b"SyntheticChild", Path(self.temp.name + "/ledger.afaenc").read_bytes())
        self.client.force_login(self.cook)
        self.assertContains(self.client.get(reverse("cafeteria:kitchen_report")), "ATURA LA PREPARACIÓ")

    def test_export_can_only_be_downloaded_by_its_requester(self):
        item = DataRequest.objects.create(requester=self.tutor, student=self.student, kind="access", message="Synthetic", due_at=timezone.now(), resolved_at=timezone.now(), export_expires_at=timezone.now() + timedelta(days=1))
        item.export_file.save("synthetic.json", ContentFile(b'{"own":"data"}'), save=True)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("cafeteria:request_export_download", args=[item.pk])).status_code, 404)
        self.client.force_login(self.tutor)
        self.assertEqual(self.client.get(reverse("cafeteria:request_export_download", args=[item.pk])).status_code, 200)

    def test_audit_does_not_copy_clinical_content_or_secrets(self):
        from .models import log_event
        event = log_event(self.tutor, "synthetic", self.student, {"title": "PRIVATE", "token": "SECRET", "email": "PRIVATE", "status": "pending"})
        self.assertEqual(event.details, {"status": "pending"})

    @override_settings(PRIVACY_ENFORCED=True)
    def test_health_declaration_uses_previously_authorised_terms(self):
        from .forms import TutorStudentForm
        from .privacy import has_health_consent
        self.publish()
        data = {"first_name": self.student.first_name, "last_name": self.student.last_name,
            "default_diet": self.diet.pk, "meal_plan": "fixed", "allergy_declaration": "yes",
            "allergy_title": "Updated synthetic diagnosis", "allergy_details": "Updated synthetic details",
            "kitchen_instructions": "Avoid eggs"}
        form = TutorStudentForm(data, instance=Student.objects.get(pk=self.student.pk), actor=self.tutor)
        self.assertTrue(form.is_valid(), form.errors)
        with self.captureOnCommitCallbacks(execute=True):
            changed = form.save()
        self.assertTrue(has_health_consent(changed))
        self.assertFalse(ConsentRecord.objects.filter(student=changed).exists())
        self.assertEqual(changed.allergy_title, "Updated synthetic diagnosis")
        self.assertEqual(len(load_restriction_ledger()), 1)
        self.assertTrue(BlockedData.objects.filter(subject=changed.privacy_id).exists())

    def test_invalid_rights_export_does_not_apply_restriction(self):
        item = DataRequest.objects.create(requester=self.tutor, student=self.student, kind="erase", message="Synthetic", due_at=timezone.now())
        self.client.force_login(self.reviewer)
        response = self.client.post(reverse("cafeteria:privacy_request_review", args=[item.pk]), {
            "response": "Synthetic response", "export_text": "not json", "reviewed": "on", "action": "restrict_student",
        })
        self.assertEqual(response.status_code, 200)
        self.student.refresh_from_db()
        self.assertTrue(self.student.active)
        self.assertEqual(load_restriction_ledger(), [])

    def test_custody_confirmation_cannot_make_old_or_deleted_copy_fresh(self):
        from .models import BackupCustody
        from .privacy import backup_overdue
        copy = BackupCustody.objects.create(confirmed_at=timezone.now(), expires_at=timezone.now() + timedelta(days=30))
        self.assertFalse(backup_overdue())
        BackupCustody.objects.filter(pk=copy.pk).update(generated_at=timezone.now() - timedelta(days=2))
        self.assertTrue(backup_overdue())
        BackupCustody.objects.filter(pk=copy.pk).update(generated_at=timezone.now(), deleted_at=timezone.now())
        self.assertTrue(backup_overdue())

    def test_legal_hold_prevents_expired_reserved_evidence_destruction(self):
        from .privacy import journal_restriction
        with self.captureOnCommitCallbacks(execute=True):
            restrict_student(self.student, actor=self.tutor)
        with self.captureOnCommitCallbacks(execute=True):
            journal_restriction(self.student, category="legal_hold", destroy_after=timezone.now())
        BlockedData.objects.update(destroy_after=timezone.now() - timedelta(days=1))
        replay_restrictions(load_restriction_ledger())
        self.assertTrue(BlockedData.objects.exists())
        with self.captureOnCommitCallbacks(execute=True):
            journal_restriction(self.student, category="release_hold", destroy_after=timezone.now())
        self.assertFalse(BlockedData.objects.exists())

    @override_settings(DATA_ENCRYPTION_ENABLED=True)
    def test_interrupted_restriction_fails_closed_until_reconciliation(self):
        from .privacy import restore_marker
        with patch("apps.cafeteria.privacy.replay_restrictions", side_effect=RuntimeError("synthetic interruption")):
            with self.assertRaises(RuntimeError):
                restrict_student(self.student, actor=self.tutor)
        self.assertTrue(restore_marker().exists())
        self.assertEqual(self.client.get(reverse("cafeteria:login")).status_code, 503)
        replay_restrictions(load_restriction_ledger())
        self.student.refresh_from_db()
        self.assertTrue(self.student.safety_hold)

    def test_csrf_is_required_for_restriction(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.tutor)
        response = client.post(reverse("cafeteria:withdraw_health_consent", args=[self.student.pk]), {"password": "Synthetic-long-password-6723"})
        self.assertEqual(response.status_code, 403)

    def test_financial_retention_removes_receipts_without_changing_amounts(self):
        from decimal import Decimal
        from .models import EconomicEntry, EconomicAttachment, EconomicCategory, FinancialAccount
        from .privacy import purge_closed_accounting
        account = FinancialAccount.objects.create(name="Synthetic account")
        category = EconomicCategory.objects.create(name="Synthetic supplies", entry_type="expense")
        old = timezone.now() - timedelta(days=365)
        entry = EconomicEntry.objects.create(account=account, category=category, entry_type="expense", amount=Decimal("12.34"),
            date=old.date(), paid_on=old.date(), concept="Private reimbursement", notes="private-note", submitted_by=self.tutor)
        EconomicEntry.objects.filter(pk=entry.pk).update(updated_at=old)
        attachment = EconomicAttachment.objects.create(entry=entry, file=ContentFile(b"receipt", name="synthetic.pdf"), original_name="synthetic.pdf")
        file_name = attachment.file.name
        cutoff = timezone.now() - timedelta(days=30)
        self.assertEqual(purge_closed_accounting(cutoff, dry_run=True)["entries"], 1)
        self.assertTrue(EconomicAttachment.objects.exists())
        with self.captureOnCommitCallbacks(execute=True):
            purge_closed_accounting(cutoff)
        entry.refresh_from_db()
        self.assertEqual(entry.amount, Decimal("12.34"))
        self.assertEqual(entry.notes, "")
        self.assertIsNone(entry.submitted_by_id)
        from django.core.files.storage import default_storage
        self.assertFalse(default_storage.exists(file_name))

    @override_settings(PRIVACY_ENFORCED=True)
    def test_csv_cannot_bypass_the_health_declaration(self):
        from .views import CSV_COLUMNS, _parse_family_csv
        import csv
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerow({"family_name": "Synthetic", "student_first_name": "Synthetic", "student_last_name": "Child", "dietary_notes": "Private diagnosis"})
        _digest, valid, errors = _parse_family_csv(io.BytesIO(output.getvalue().encode()), self.year)
        self.assertFalse(valid)
        self.assertTrue(errors)

    def test_production_logs_do_not_include_payloads_or_exception_text(self):
        import logging
        from .logging import MetadataOnlyFilter
        record = logging.LogRecord("django.request", logging.ERROR, __file__, 1, "Patient %s", ("Private name",),
            (ValueError, ValueError("private diagnosis"), None))
        MetadataOnlyFilter().filter(record)
        self.assertNotIn("Private", record.getMessage())
        self.assertNotIn("diagnosis", record.getMessage())
        self.assertIsNone(record.exc_info)

    @override_settings(MFA_REQUIRED=True)
    def test_only_superuser_access_requires_second_factor(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("cafeteria:people"))
        self.assertRedirects(response, reverse("cafeteria:mfa_verify"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("cafeteria:privacy_notice")).status_code, 200)

    @override_settings(MFA_REQUIRED=True)
    def test_staff_role_access_does_not_require_second_factor(self):
        self.client.force_login(self.cook)
        response = self.client.get(reverse("cafeteria:kitchen_report"))
        self.assertEqual(response.status_code, 200)

    @override_settings(MFA_REQUIRED=True)
    def test_totp_and_recovery_codes_cannot_be_replayed(self):
        from django.contrib.auth.hashers import make_password
        device = TOTPDevice.objects.create(user=self.admin, confirmed=True)
        self.client.force_login(self.admin)
        token = str(totp(device.bin_key)).zfill(6)
        response = self.client.post(reverse("cafeteria:mfa_verify"), {"token": token})
        self.assertEqual(response.status_code, 302)
        self.client.logout()
        self.client.force_login(self.admin)
        response = self.client.post(reverse("cafeteria:mfa_verify"), {"token": token})
        self.assertContains(response, "Codi incorrecte")
        code = "abcd1234abcd1234abcd"
        RecoveryCode.objects.create(user=self.admin, digest=make_password(code))
        self.assertEqual(self.client.post(reverse("cafeteria:mfa_verify"), {"token": code}).status_code, 302)
        self.client.logout()
        self.client.force_login(self.admin)
        self.assertContains(self.client.post(reverse("cafeteria:mfa_verify"), {"token": code}), "Codi incorrecte")


@override_settings(MFA_REQUIRED=False, PRIVACY_ENFORCED=False)
class EncryptedBackupTests(TransactionTestCase):
    def test_full_encrypted_backup_and_restore(self):
        if settings.DATABASE_ENGINE != "config.sqlcipher":
            self.skipTest("Run this suite with DATA_ENCRYPTION_ENABLED=true")
        from .backups import build_encrypted_backup, extract_encrypted_backup
        from .views import _validate_restore_database, _restore_portal_state
        with tempfile.TemporaryDirectory() as work, override_settings(MEDIA_ROOT=work + "/media", PRIVATE_TEMP_DIR=work, PRIVACY_LEDGER_PATH=work + "/ledger.afaenc"):
            from django.core.files.storage import default_storage
            name = default_storage.save("medical/synthetic.pdf", ContentFile(b"%PDF recoverable-secret"))
            family = Family.objects.create(name="Recoverable synthetic family")
            with build_encrypted_backup() as backup:
                payload = backup.read()
            self.assertNotIn(b"Recoverable synthetic family", payload)
            self.assertNotIn(b"recoverable-secret", payload)
            stage, db, manifest = extract_encrypted_backup(io.BytesIO(payload))
            try:
                _validate_restore_database(db, key_id=manifest["database_key"])
                Family.objects.filter(pk=family.pk).update(name="Changed")
                _restore_portal_state(db, stage, key_id=manifest["database_key"])
                self.assertEqual(Family.objects.get(pk=family.pk).name, "Recoverable synthetic family")
                self.assertEqual(default_storage.open(name).read(), b"%PDF recoverable-secret")
            finally:
                import shutil
                shutil.rmtree(stage)

    def test_old_backup_cannot_resurrect_later_restricted_health(self):
        if not settings.DATA_ENCRYPTION_ENABLED:
            self.skipTest("Encrypted recovery integration")
        from .backups import build_encrypted_backup, extract_encrypted_backup
        from .privacy import finish_restore, mark_restore_pending, restore_marker
        from .views import _restore_portal_state
        with tempfile.TemporaryDirectory() as work, override_settings(MEDIA_ROOT=work + "/media", PRIVATE_TEMP_DIR=work, PRIVACY_LEDGER_PATH=work + "/ledger.afaenc"):
            family = Family.objects.create(name="Synthetic recovery family")
            diet = Diet.objects.create(name="Synthetic diet")
            student = Student.objects.create(family=family, default_diet=diet, first_name="Synthetic", last_name="Child", has_allergy=True, allergy_details="Old clinical content")
            RetentionRule.objects.create(category="health", days=30, justification="Synthetic")
            with build_encrypted_backup() as backup:
                payload = backup.read()
            restrict_student(student, actor=None)
            stage, db, manifest = extract_encrypted_backup(io.BytesIO(payload))
            try:
                mark_restore_pending()
                _restore_portal_state(db, stage, key_id=manifest["database_key"])
                self.assertEqual(Student.objects.get(pk=student.pk).allergy_details, "Old clinical content")
                finish_restore()
                restored = Student.objects.get(pk=student.pk)
                self.assertEqual(restored.allergy_details, "")
                self.assertTrue(restored.safety_hold)
                self.assertIsNone(restored.default_diet_id)
                self.assertTrue(restore_marker().exists())  # offline access review still mandatory
            finally:
                import shutil
                shutil.rmtree(stage)
