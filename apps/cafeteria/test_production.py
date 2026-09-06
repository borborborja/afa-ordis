"""Regression coverage for production audit findings."""
import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import tempfile
from threading import Barrier
from unittest.mock import patch
import zipfile

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, connection, close_old_connections
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone, translation

from .forms import AcademicHolidayForm, AcademicYearForm, CourseClosureForm, MealSettingsForm, TutorStudentForm
from .maintenance import PortalBusy, portal_lock
from .models import (
    AcademicHoliday, AcademicYear, AllergyReviewStatus, BookingStatus, CourseGroup,
    DailyReport, DailyReportRecipient, Diet, EconomicEntry, EconomicEntryType,
    EconomicPaymentStatus, EconomicReviewStatus, Family, FamilyMembership,
    FinancialAccount, EconomicCategory, Invitation, MealBooking, MealPlan,
    MealSettings, MonthlyPreparation, PriceRule, ServiceDay, StatementStatus,
    Student, TeacherMealBooking, TeacherMealProfile,
)
from .services import is_tutor_locked, prepare_monthly_statement
from .tasks import prepare_due_monthly_statements, send_daily_report, send_due_daily_reports
from .views import _extract_portal_backup, _restore_portal_state, _restore_sqlite_database


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ProductionRegressionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.year = AcademicYear.objects.create(
            name="2026-2027", starts_on=cls.today - timedelta(days=60),
            ends_on=cls.today + timedelta(days=365), is_active=True,
        )
        cls.family = Family.objects.create(name="Família de prova")
        cls.diet = Diet.objects.create(name="Ordinària")
        cls.group = CourseGroup.objects.create(academic_year=cls.year, name="I4")
        cls.tutor = User.objects.create_user("family@example.test", "family@example.test", "audit-correct-horse-battery-staple")
        cls.admin = User.objects.create_superuser("admin@example.test", "admin@example.test", "audit-correct-horse-battery-staple")
        FamilyMembership.objects.create(user=cls.tutor, family=cls.family)
        cls.student = Student.objects.create(
            family=cls.family, course_group=cls.group, default_diet=cls.diet,
            first_name="Laia", last_name="Puig", meal_plan=MealPlan.FIXED,
        )
        cls.day = ServiceDay.objects.create(academic_year=cls.year, date=cls.today)
        cls.price = PriceRule.objects.create(
            scholarship=False, meal_plan=MealPlan.FIXED,
            effective_from=cls.today - timedelta(days=60), amount=Decimal("6.50"),
        )

    def setUp(self):
        translation.activate("ca")
        cache.clear()
        self.addCleanup(cache.clear)
        self.addCleanup(translation.deactivate)

    def medical_form(self, **changes):
        data = {
            "first_name": "Laia", "last_name": "Puig", "default_diet": self.diet.id,
            "meal_plan": MealPlan.FIXED, "allergy_declaration": "yes",
            "allergy_title": "Fruits secs", "allergy_details": "Evitar traces",
        }
        data.update(changes)
        return TutorStudentForm(data, instance=self.student)

    def store_medical_document(self):
        self.student.has_allergy = True
        self.student.allergy_title = "Fruits secs"
        self.student.allergy_details = "Evitar traces"
        self.student.allergy_review_status = AllergyReviewStatus.APPROVED
        self.student.allergy_document = SimpleUploadedFile("medical.pdf", b"%PDF-1.4 medical")
        self.student.allergy_document_name = "medical.pdf"
        self.student.save()
        return self.student.allergy_document.name

    def test_editing_contact_keeps_medical_document_and_approval(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            filename = self.store_medical_document()
            form = self.medical_form(contact_phone="600111222")
            self.assertTrue(form.is_valid(), form.errors)
            with self.captureOnCommitCallbacks(execute=True):
                form.save()
            self.student.refresh_from_db()
            self.assertEqual(self.student.allergy_review_status, AllergyReviewStatus.APPROVED)
            self.assertEqual(self.student.allergy_document_name, "medical.pdf")
            self.assertTrue((Path(media) / filename).is_file())

    def test_changed_allergy_requires_review_without_deleting_existing_document(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            filename = self.store_medical_document()
            form = self.medical_form(allergy_title="Llet", allergy_declaration="")
            self.assertTrue(form.is_valid(), form.errors)
            with self.captureOnCommitCallbacks(execute=True):
                form.save()
            self.student.refresh_from_db()
            self.assertEqual(self.student.allergy_review_status, AllergyReviewStatus.PENDING)
            self.assertTrue((Path(media) / filename).is_file())

    def test_rejected_allergy_requires_a_new_upload(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            self.store_medical_document()
            self.student.allergy_review_status = AllergyReviewStatus.REJECTED
            self.student.save()
            form = self.medical_form()
            self.assertFalse(form.is_valid())
            self.assertIn("allergy_document", form.errors)

    def test_past_bookings_are_locked_even_without_cutoff_settings(self):
        self.assertTrue(is_tutor_locked(self.today - timedelta(days=1)))
        MealSettings.objects.create(academic_year=self.year, daily_cutoff=None)
        self.assertTrue(is_tutor_locked(self.today - timedelta(days=1)))
        self.assertFalse(is_tutor_locked(self.today + timedelta(days=1)))

    def test_closing_service_cancels_both_student_and_teacher_bookings(self):
        booking = MealBooking.objects.create(student=self.student, date=self.today, diet=self.diet)
        teacher = TeacherMealProfile.objects.create(user=self.admin, default_diet=self.diet)
        teacher_booking = TeacherMealBooking.objects.create(teacher=teacher, date=self.today, diet=self.diet)
        self.client.force_login(self.admin)
        self.client.post(reverse("cafeteria:service_day_by_date_toggle", args=[self.year.id, self.today.isoformat()]), {"is_service_day": "0"})
        booking.refresh_from_db()
        teacher_booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CANCELLED)
        self.assertEqual(teacher_booking.status, BookingStatus.CANCELLED)

    def test_closing_statement_refreshes_prepared_lines(self):
        statement = prepare_monthly_statement(self.family, self.today.year, self.today.month)
        MealBooking.objects.create(student=self.student, date=self.today, diet=self.diet)
        self.client.force_login(self.admin)
        self.client.post(reverse("cafeteria:statement_close", args=[statement.id]))
        statement.refresh_from_db()
        self.assertEqual(statement.total, Decimal("6.50"))
        self.assertEqual(statement.status, StatementStatus.CLOSED)

    def test_missing_tariff_blocks_statement_closure(self):
        statement = prepare_monthly_statement(self.family, self.today.year, self.today.month)
        self.price.delete()
        MealBooking.objects.create(student=self.student, date=self.today, diet=self.diet)
        self.client.force_login(self.admin)
        self.client.post(reverse("cafeteria:statement_close", args=[statement.id]))
        statement.refresh_from_db()
        self.assertEqual(statement.status, StatementStatus.PREPARED)

    def test_closed_statement_blocks_booking_changes(self):
        MealBooking.objects.create(student=self.student, date=self.today, diet=self.diet)
        statement = prepare_monthly_statement(self.family, self.today.year, self.today.month)
        statement.status = StatementStatus.CLOSED
        statement.save()
        self.client.force_login(self.tutor)
        response = self.client.post(reverse("cafeteria:family_booking_update", args=[self.family.id]), {
            "student_id": self.student.id, "service_date": self.today.isoformat(), "operation": "cancel",
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(MealBooking.objects.get(student=self.student).status, BookingStatus.ACTIVE)

    def test_calendar_queries_do_not_grow_with_siblings(self):
        month_start = self.today.replace(day=1)
        for offset in range(28):
            ServiceDay.objects.get_or_create(academic_year=self.year, date=month_start + timedelta(days=offset))
        self.client.force_login(self.tutor)
        url = reverse("cafeteria:family_calendar", args=[self.family.id])
        self.client.get(url)  # establish active-family session preference
        with CaptureQueriesContext(connection) as single:
            self.assertEqual(self.client.get(url).status_code, 200)
        for index in range(4):
            Student.objects.create(family=self.family, course_group=self.group, default_diet=self.diet, first_name=f"Infant {index}", last_name="Puig")
        with CaptureQueriesContext(connection) as siblings:
            self.assertEqual(self.client.get(url).status_code, 200)
        self.assertLessEqual(len(siblings), len(single) + 2)
        self.assertLess(len(siblings), 40)
        print(f"Calendar queries: one child={len(single)}, five children={len(siblings)}")

    def settings_for_daily_mail(self):
        meal_settings = MealSettings.objects.create(academic_year=self.year, daily_reports_enabled=True, daily_report_send_time=time(0, 0))
        DailyReportRecipient.objects.create(settings=meal_settings, email=self.admin.email, user=self.admin)

    def test_scheduler_retries_a_failed_daily_email(self):
        self.settings_for_daily_mail()
        with patch("apps.cafeteria.tasks.send_mail", side_effect=OSError("SMTP unavailable")):
            with self.assertLogs("apps.cafeteria.tasks", level="ERROR"):
                self.assertEqual(send_due_daily_reports(), 0)
        self.assertIsNone(DailyReport.objects.get(date=self.today).sent_at)
        self.assertEqual(send_due_daily_reports(), 1)
        self.assertEqual(send_due_daily_reports(), 0)

    def test_daily_report_is_not_marked_sent_when_backend_sends_zero(self):
        self.settings_for_daily_mail()
        with patch("apps.cafeteria.tasks.send_mail", return_value=0):
            self.assertFalse(send_daily_report(self.today.isoformat()))
        self.assertIsNone(DailyReport.objects.get(date=self.today).sent_at)

    def test_holidays_cannot_trigger_a_manual_daily_email(self):
        self.settings_for_daily_mail()
        AcademicHoliday.objects.create(academic_year=self.year, title=self.year.name, starts_on=self.today, ends_on=self.today)
        self.assertFalse(send_daily_report(self.today.isoformat()))

    def test_monthly_scheduler_catches_up_only_once(self):
        MealSettings.objects.create(academic_year=self.year, monthly_preparation_day=1, monthly_preparation_hour=time(8))
        now = timezone.make_aware(datetime.combine(self.today.replace(day=3), time(10)))
        with patch("apps.cafeteria.tasks.timezone.localtime", return_value=now):
            self.assertEqual(prepare_due_monthly_statements(), 1)
            self.assertEqual(prepare_due_monthly_statements(), 0)
        self.assertEqual(MonthlyPreparation.objects.count(), 1)

    def test_existing_account_invitation_requires_csrf_protected_post(self):
        invitation = Invitation.objects.create(email=self.tutor.email, role="admin")
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.tutor)
        url = reverse("cafeteria:invitation_accept", args=[invitation.token])
        self.assertEqual(client.get(url).status_code, 200)
        invitation.refresh_from_db()
        self.assertIsNone(invitation.accepted_at)
        self.assertFalse(self.tutor.groups.filter(name="admin").exists())
        self.assertEqual(client.post(url).status_code, 403)
        response = client.post(url, {"csrfmiddlewaretoken": client.cookies["csrftoken"].value})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.tutor.groups.filter(name="admin").exists())

    @override_settings(AUTH_RATE_LIMIT=2)
    def test_login_is_throttled_and_email_case_is_normalized(self):
        url = reverse("cafeteria:login")
        for _ in range(2):
            self.assertEqual(self.client.post(url, {"username": self.tutor.email, "password": "wrong"}).status_code, 200)
        self.assertEqual(self.client.post(url, {"username": self.tutor.email.upper(), "password": "wrong"}).status_code, 429)
        cache.clear()
        self.assertEqual(self.client.post(url, {"username": self.tutor.email.upper(), "password": "audit-correct-horse-battery-staple"}).status_code, 302)

    def test_csrf_forms_keep_the_origin_without_leaking_private_paths(self):
        login = self.client.get(reverse("cafeteria:login"))
        self.assertEqual(login["Referrer-Policy"], "strict-origin")
        self.client.force_login(self.tutor)
        private_page = self.client.get(reverse("cafeteria:family_calendar", args=[self.family.id]))
        self.assertEqual(private_page["Referrer-Policy"], "strict-origin")
        self.assertIn("no-store", private_page["Cache-Control"])

    def test_private_pages_are_not_cacheable_and_media_is_never_public(self):
        self.client.force_login(self.tutor)
        response = self.client.get(reverse("cafeteria:family_calendar", args=[self.family.id]))
        self.assertIn("no-store", response["Cache-Control"])
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            name = self.store_medical_document()
            self.client.logout()
            self.assertEqual(self.client.get("/media/" + name).status_code, 404)

    def test_bad_identifiers_and_years_do_not_cause_server_errors(self):
        self.client.force_login(self.admin)
        for route, params in (
            ("economic_configuration", {"account": "abc", "category": "abc"}),
            ("economic_entries", {"academic_year": "abc"}),
            ("economic_entries", {"period": "calendar", "year": "999999999999"}),
            ("school_calendar", {"year": "abc"}),
            ("course_management", {"year": "abc"}),
            ("afa_memberships", {"year": "abc"}),
        ):
            with self.subTest(route=route, params=params):
                self.assertLess(self.client.get(reverse("cafeteria:" + route), params).status_code, 500)
        response = self.client.post(reverse("cafeteria:family_booking_update", args=[self.family.id]), {"student_id": "abc"})
        self.assertEqual(response.status_code, 404)

    def test_paid_redirect_cannot_leave_the_portal(self):
        account = FinancialAccount.objects.get(name="Compte bancari AFA")
        category = EconomicCategory.objects.get(name="Material", entry_type=EconomicEntryType.EXPENSE)
        entry = EconomicEntry.objects.create(
            account=account, category=category, entry_type=EconomicEntryType.EXPENSE,
            concept="Material", amount=1, review_status=EconomicReviewStatus.APPROVED,
            payment_status=EconomicPaymentStatus.PAID, paid_on=self.today,
        )
        self.client.force_login(self.admin)
        response = self.client.post(reverse("cafeteria:economic_mark_paid", args=[entry.id]), {"next": "https://evil.example/"})
        self.assertEqual(response.url, reverse("cafeteria:economic_entries"))

    def test_opening_balance_excludes_older_payments(self):
        account = FinancialAccount.objects.get(name="Compte bancari AFA")
        account.opening_balance = Decimal("100.00")
        account.opening_balance_date = self.today
        account.save()
        category = EconomicCategory.objects.get(name="Material", entry_type=EconomicEntryType.EXPENSE)
        for paid_on in (self.today - timedelta(days=1), self.today):
            EconomicEntry.objects.create(account=account, category=category, entry_type=EconomicEntryType.EXPENSE, concept="Material", amount=10, review_status="approved", payment_status="paid", paid_on=paid_on)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("cafeteria:economic_dashboard"))
        row = next(row for row in response.context["account_rows"] if row["account"].id == account.id)
        self.assertEqual(row["balance"], Decimal("90.00"))

    def test_csv_escapes_formulas_in_user_supplied_names(self):
        self.student.first_name = "=1+1"
        self.student.save()
        MealBooking.objects.create(student=self.student, date=self.today, diet=self.diet)
        statement = prepare_monthly_statement(self.family, self.today.year, self.today.month)
        self.client.force_login(self.tutor)
        response = self.client.get(reverse("cafeteria:statement_csv", args=[statement.id]))
        rows = list(csv.reader(StringIO(response.content.decode("utf-8-sig"))))
        self.assertTrue(rows[1][1].startswith("'=1+1"))

    def test_invalid_academic_forms_return_validation_errors(self):
        for form_class in (AcademicYearForm, AcademicHolidayForm, CourseClosureForm, MealSettingsForm):
            with self.subTest(form=form_class):
                self.assertFalse(form_class({"starts_on": "invalid", "date": "invalid", "monthly_preparation_day": "invalid"}).is_valid())

    def test_academic_years_cannot_overlap(self):
        form = AcademicYearForm({"name": "2027-2028", "starts_on": self.today, "ends_on": self.today + timedelta(days=400)})
        self.assertFalse(form.is_valid())

    def test_duplicate_daily_recipient_is_a_form_error(self):
        self.settings_for_daily_mail()
        self.client.force_login(self.admin)
        response = self.client.post(reverse("cafeteria:meal_configuration"), {
            "intent": "recipient", "recipient-email": self.admin.email.upper(), "recipient-active": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["recipient_form"].errors)
        self.assertEqual(DailyReportRecipient.objects.count(), 1)

    def test_rejecting_an_expense_without_reason_is_a_form_error(self):
        category = EconomicCategory.objects.get(name="Material", entry_type=EconomicEntryType.EXPENSE)
        entry = EconomicEntry.objects.create(category=category, entry_type=EconomicEntryType.EXPENSE, concept="Material", amount=10, review_status="submitted", payment_status="pending")
        self.client.force_login(self.admin)
        response = self.client.post(reverse("cafeteria:economic_review", args=[entry.id]), {
            "entry_type": "expense", "date": self.today.isoformat(), "concept": "Material", "amount": "10.00",
            "category": category.id, "payment_status": "pending", "action": "reject", "rejected_reason": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        entry.refresh_from_db()
        self.assertEqual(entry.review_status, "submitted")

    def test_extreme_calendar_months_do_not_overflow(self):
        self.client.force_login(self.tutor)
        for month in ("0001-01", "9999-12"):
            self.assertEqual(self.client.get(reverse("cafeteria:family_calendar", args=[self.family.id]), {"month": month}).status_code, 200)

    def test_health_reports_database_failures(self):
        self.assertEqual(self.client.get("/health/").status_code, 200)
        with patch("apps.cafeteria.views.connection.cursor", side_effect=DatabaseError("schema unavailable")):
            self.assertEqual(self.client.get("/health/").status_code, 503)


class BackupSafetyTests(SimpleTestCase):
    def test_archive_rejects_traversal_invalid_manifests_and_duplicates(self):
        for manifest, extra_name in (([], "media/a.pdf"), ({"format": "afa-ordis-backup", "version": 1}, "media/../../outside"), ({"format": "afa-ordis-backup", "version": 1}, "database.sqlite3")):
            with self.subTest(manifest=manifest, extra_name=extra_name), tempfile.NamedTemporaryFile(suffix=".zip") as upload:
                with zipfile.ZipFile(upload, "w") as archive:
                    archive.writestr("backup.json", json.dumps(manifest))
                    archive.writestr("database.sqlite3", b"database")
                    archive.writestr(extra_name, b"unexpected")
                upload.flush()
                with self.assertRaises(ValueError):
                    _extract_portal_backup(upload.name)

    def test_restore_lock_excludes_other_requests_and_scheduler(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(PORTAL_LOCK_PATH=str(Path(directory) / "portal.lock")):
            with portal_lock():
                with portal_lock():
                    pass  # ordinary requests may run concurrently
                with self.assertRaises(PortalBusy):
                    with portal_lock(exclusive=True):
                        pass
            with portal_lock(exclusive=True):
                with self.assertRaises(PortalBusy):
                    with portal_lock():
                        pass


class FileDatabaseIntegrationTests(TransactionTestCase):
    """Run with DATABASE_TEST_NAME to exercise real SQLite WAL connections."""
    def setUp(self):
        if "mode=memory" in str(connection.settings_dict["NAME"]):
            self.skipTest("Set DATABASE_TEST_NAME to run SQLite file/concurrency integration tests.")
        self.media = tempfile.TemporaryDirectory(prefix="afa-audit-media-")
        self.addCleanup(self.media.cleanup)
        self.ledger = tempfile.TemporaryDirectory(prefix="afa-audit-ledger-")
        self.addCleanup(self.ledger.cleanup)
        override = override_settings(MEDIA_ROOT=self.media.name, PRIVACY_LEDGER_PATH=self.ledger.name + "/ledger.afaenc")
        override.enable()
        self.addCleanup(override.disable)
        self.admin = User.objects.create_superuser("restore@example.test", "restore@example.test", "audit-restore-correct-password")
        self.client.force_login(self.admin)

    def backup(self):
        response = self.client.post(reverse("cafeteria:portal_backup_download"))
        self.assertEqual(response.status_code, 200)
        payload = b"".join(response.streaming_content)
        response.close()
        return payload

    def write_media(self, path, content):
        from django.conf import settings
        from .crypto import encrypt_stream
        from io import BytesIO
        with path.open("wb") as target:
            if settings.DATA_ENCRYPTION_ENABLED:
                encrypt_stream(BytesIO(content), target, purpose="media", context=path.relative_to(self.media.name).as_posix().encode())
            else:
                target.write(content)

    def read_media(self, path):
        from django.core.files.storage import default_storage
        with default_storage.open(path.relative_to(self.media.name).as_posix()) as source:
            return source.read()

    def test_complete_restore_replaces_database_and_documents_and_logs_out(self):
        Family.objects.create(name="Before")
        medical = Path(self.media.name) / "medical.pdf"
        self.write_media(medical, b"original document")
        payload = self.backup()
        Family.objects.create(name="After")
        self.write_media(medical, b"modified document")
        orphan = Path(self.media.name) / "orphan.pdf"
        self.write_media(orphan, b"after snapshot")
        from .crypto import encrypt_stream
        from io import BytesIO
        ledger = BytesIO()
        encrypt_stream(BytesIO(b"[]"), ledger, purpose="backup", context=b"restriction-ledger") if settings.DATA_ENCRYPTION_ENABLED else None
        response = self.client.post(reverse("cafeteria:portal_restore"), {
            "backup_file": SimpleUploadedFile("snapshot.zip", payload),
            "confirmation": "RESTAURA", "password": "audit-restore-correct-password",
            "restriction_ledger": SimpleUploadedFile("ledger.afaenc", ledger.getvalue()), "latest_ledger_confirmed": "on",
        })
        self.assertEqual(response.url, reverse("cafeteria:login"))
        self.assertTrue(Family.objects.filter(name="Before").exists())
        self.assertFalse(Family.objects.filter(name="After").exists())
        self.assertEqual(self.read_media(medical), b"original document")
        self.assertEqual(medical.stat().st_mode & 0o777, 0o600)
        self.assertFalse(orphan.exists())
        self.assertNotIn("_auth_user_id", self.client.session)
        from .privacy import restore_marker, ledger_path
        restore_marker().unlink(missing_ok=True)
        ledger_path().unlink(missing_ok=True)

    def test_failed_restore_rolls_back_database_and_documents(self):
        medical = Path(self.media.name) / "medical.pdf"
        self.write_media(medical, b"original")
        payload = self.backup()
        self.write_media(medical, b"current")
        Family.objects.create(name="Must survive")
        with tempfile.NamedTemporaryFile(suffix=".zip") as uploaded:
            uploaded.write(payload)
            uploaded.flush()
            if settings.DATA_ENCRYPTION_ENABLED:
                from .backups import extract_encrypted_backup
                with open(uploaded.name, "rb") as source:
                    staging, database, _manifest = extract_encrypted_backup(source)
            else:
                staging, database = _extract_portal_backup(uploaded.name)
        try:
            attempts = 0

            def restore_then_fail(path, key_id=None):
                nonlocal attempts
                attempts += 1
                _restore_sqlite_database(path, key_id=key_id)
                if attempts == 1:
                    raise OSError("injected failure after replacing database")

            with patch("apps.cafeteria.views._restore_sqlite_database", side_effect=restore_then_fail) as restore:
                with self.assertRaises(OSError):
                    _restore_portal_state(database, staging)
                self.assertEqual(restore.call_count, 2)
            self.assertEqual(self.read_media(medical), b"current")
            self.assertTrue(Family.objects.filter(name="Must survive").exists())
        finally:
            import shutil
            shutil.rmtree(staging)

    def test_concurrent_retries_create_exactly_one_booking(self):
        today = timezone.localdate()
        year = AcademicYear.objects.create(name="2026-2027", starts_on=today - timedelta(days=1), ends_on=today + timedelta(days=365))
        family = Family.objects.create(name="Concurrent")
        diet = Diet.objects.create(name="Ordinària")
        student = Student.objects.create(family=family, default_diet=diet, first_name="Laia", last_name="Puig")
        ServiceDay.objects.create(academic_year=year, date=today)
        url = reverse("cafeteria:family_booking_update", args=[family.id])
        clients = [Client() for _ in range(4)]
        for client in clients:
            client.force_login(self.admin)
        barrier = Barrier(4)

        def reserve(client):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return client.post(url, {"student_id": student.id, "service_date": today.isoformat(), "operation": "reserve"}).status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(reserve, clients)), [200] * 4)
        self.assertEqual(MealBooking.objects.filter(student=student, date=today).count(), 1)
