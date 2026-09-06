from datetime import datetime, time, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import tempfile
import zipfile

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.template.loader import get_template
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils import translation

from .forms import TutorStudentForm
from .models import (
    AcademicHoliday,
    AcademicIntensivePeriod,
    AcademicNotice,
    AcademicYear,
    AllergyReviewStatus,
    AfaFeeSettings,
    AfaMembership,
    AfaMembershipStatus,
    BookingStatus,
    CourseClosure,
    CourseGroup,
    DailyReportRecipient,
    Diet,
    EconomicAttachment,
    EconomicCategory,
    EconomicEntry,
    EconomicEntryType,
    EconomicPaymentStatus,
    EconomicReviewStatus,
    FinancialAccount,
    Family,
    FamilyMembership,
    FamilyImportBatch,
    Invitation,
    MealBooking,
    MealPlan,
    MealSettings,
    PortalSettings,
    PriceRule,
    ServiceDay,
    Student,
    TeacherMealBooking,
    TeacherMealProfile,
)
from .i18n_audit import audit_catalog, audit_javascript, audit_project, audit_python, audit_templates
from .services import (
    build_daily_report_text,
    expected_report_is_due,
    is_service_day,
    prepare_monthly_statement,
    prepare_teacher_monthly_statement,
)
from .tasks import send_daily_report


class I18nAuditTests(SimpleTestCase):
    def test_current_portal_passes_the_strict_language_audit(self):
        self.assertEqual(audit_project(settings.BASE_DIR), [])

    def test_catalog_audit_rejects_an_untranslated_or_fuzzy_entry(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            catalog = Path(temporary_directory) / "django.po"
            catalog.write_text(
                'msgid ""\nmsgstr "Language: es\\n"\n\n#, fuzzy\nmsgid "Desa"\nmsgstr ""\n',
                encoding="utf-8",
            )
            errors = audit_catalog(catalog, "es")
        self.assertTrue(any("fuzzy" in error for error in errors))
        self.assertTrue(any("falta la traducció" in error for error in errors))

    def test_template_javascript_and_report_audits_reject_untranslated_visible_text(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            template = root / "bad.html"
            script = root / "bad.js"
            report_builder = root / "services.py"
            template.write_text("<button>Save changes</button>", encoding="utf-8")
            script.write_text("element.textContent = 'Save changes';", encoding="utf-8")
            report_builder.write_text('lines.append("Save changes")', encoding="utf-8")
            template_errors = audit_templates(root)
            javascript_errors = audit_javascript(root)
            python_errors = audit_python(root)
        self.assertTrue(any("Save changes" in error for error in template_errors))
        self.assertTrue(any("Save changes" in error for error in javascript_errors))
        self.assertTrue(any("Save changes" in error for error in python_errors))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CafeteriaFlowTests(TestCase):
    def setUp(self):
        translation.activate("ca")
        self.today = timezone.localdate()
        self.year = AcademicYear.objects.create(
            name="2026-2027",
            starts_on=self.today - timedelta(days=30),
            ends_on=self.today + timedelta(days=365),
            is_active=True,
        )
        self.group = CourseGroup.objects.create(academic_year=self.year, name="I4")
        self.family = Family.objects.create(name="Família Puig")
        self.user = User.objects.create_user("tutor@example.com", "tutor@example.com", "correct-horse-battery-staple")
        FamilyMembership.objects.create(family=self.family, user=self.user)
        self.diet = Diet.objects.create(name="Ordinària")
        self.student = Student.objects.create(
            family=self.family,
            course_group=self.group,
            first_name="Núria",
            last_name="Puig",
            default_diet=self.diet,
            meal_plan=MealPlan.FIXED,
        )
        ServiceDay.objects.create(academic_year=self.year, date=self.today, is_service_day=True)
        PriceRule.objects.create(scholarship=False, meal_plan=MealPlan.FIXED, effective_from=self.today - timedelta(days=10), amount=Decimal("6.50"))

    def tearDown(self):
        translation.deactivate()

    def test_tutor_can_create_a_booking_for_own_child(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("cafeteria:bulk_booking", args=[self.family.id]), {
            "student_id": self.student.id,
            "dates": [self.today.isoformat()],
            "action": "add",
        })
        self.assertEqual(response.status_code, 302)
        booking = MealBooking.objects.get(student=self.student, date=self.today)
        self.assertEqual(booking.status, BookingStatus.ACTIVE)
        self.assertEqual(booking.unit_price, Decimal("6.50"))
        self.assertEqual(booking.diet_name, "Ordinària")

    def test_operational_report_and_csv_follow_the_selected_language(self):
        booking = MealBooking.objects.create(student=self.student, date=self.today, diet=self.diet)
        statement = prepare_monthly_statement(self.family, self.today.year, self.today.month)
        self.client.force_login(self.user)
        with translation.override("es"):
            report = build_daily_report_text(self.today)
            response = self.client.get(reverse("cafeteria:statement_csv", args=[statement.id]))
        self.assertIn("Lista del comedor", report)
        self.assertIn("ATENCIÓN — ALERGIAS", report)
        self.assertIn("No hay alergias declaradas", report)
        self.assertContains(response, "Alumno/a")
        self.assertContains(response, "Becado/a")
        self.assertContains(response, "Importe")
        self.assertEqual(booking.status, BookingStatus.ACTIVE)

    def test_joint_booking_keeps_each_student_diet_during_an_excursion(self):
        sibling = Student.objects.create(
            family=self.family, course_group=self.group, first_name="Biel", last_name="Puig",
            default_diet=self.diet, meal_plan=MealPlan.FIXED,
        )
        CourseClosure.objects.create(course_group=self.group, date=self.today, title="Excursió")
        self.client.force_login(self.user)
        response = self.client.post(reverse("cafeteria:family_bulk_booking", args=[self.family.id]), {
            f"dates_{self.student.id}": [self.today.isoformat()],
            "copy_to_all": "1", "copy_from": self.student.id, "action": "add",
        })
        self.assertEqual(response.status_code, 302)
        bookings = MealBooking.objects.filter(student__in=[self.student, sibling], date=self.today)
        self.assertEqual(bookings.count(), 2)
        self.assertTrue(all(booking.diet == self.diet for booking in bookings))

    def test_monthly_booking_calendar_shows_all_active_children_together(self):
        sibling = Student.objects.create(
            family=self.family, course_group=self.group, first_name="Biel", last_name="Puig",
            default_diet=self.diet, meal_plan=MealPlan.FIXED,
        )
        self.client.force_login(self.user)
        response = self.client.get(f"{reverse('cafeteria:family_calendar', args=[self.family.id])}?month={self.today:%Y-%m}")
        self.assertContains(response, "Calendari mensual de reserves")
        self.assertContains(response, self.student.full_name)
        self.assertContains(response, sibling.full_name)
        self.assertContains(response, "family-booking-calendars")
        self.assertContains(response, "Reserva per a tota la família")
        self.assertNotContains(response, "monthly-booking-scroll")

    def test_family_booking_calendar_uses_tabs_by_default_and_has_visible_states(self):
        sibling = Student.objects.create(
            family=self.family, course_group=self.group, first_name="Biel", last_name="Puig",
            default_diet=self.diet, meal_plan=MealPlan.FIXED,
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("cafeteria:family_calendar", args=[self.family.id]))
        self.assertContains(response, "family-booking-tabs")
        self.assertContains(response, f'student-tab-{self.student.id}')
        self.assertContains(response, f'student-tab-{sibling.id}')
        self.assertContains(response, 'data-state="empty"')
        self.assertContains(response, 'booking-day-diet')

    def test_family_booking_matrix_can_be_selected_from_the_calendar(self):
        sibling = Student.objects.create(
            family=self.family, course_group=self.group, first_name="Biel", last_name="Puig",
            default_diet=self.diet, meal_plan=MealPlan.FIXED,
        )
        self.client.force_login(self.user)
        calendar_url = reverse("cafeteria:family_calendar", args=[self.family.id])
        response = self.client.post(
            f"{calendar_url}?month={self.today:%Y-%m}",
            {"family_booking_view": "matrix"},
        )
        self.assertRedirects(response, f"{calendar_url}?month={self.today:%Y-%m}")
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.family_booking_view, "matrix")
        response = self.client.get(calendar_url)
        self.assertContains(response, "family-matrix-calendar")
        self.assertContains(response, "matrix-student-control")
        self.assertContains(response, self.student.full_name)
        self.assertContains(response, sibling.full_name)

    def test_family_calendar_highlights_a_non_default_menu(self):
        alternative_diet = Diet.objects.create(name="Sense gluten")
        MealBooking.objects.create(
            student=self.student,
            date=self.today,
            diet=alternative_diet,
            diet_name=alternative_diet.name,
        )
        self.client.force_login(self.user)
        response = self.client.get(
            f"{reverse('cafeteria:family_calendar', args=[self.family.id])}?month={self.today:%Y-%m}"
        )
        self.assertContains(response, "Menú diferent de l'habitual")
        self.assertContains(response, 'data-diet-changed="true"')

    def test_family_can_declare_an_allergy_only_with_a_medical_document(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("cafeteria:student_edit", args=[self.student.id]), {
            "first_name": self.student.first_name,
            "last_name": self.student.last_name,
            "birth_date": "",
            "contact_email": "",
            "contact_phone": "",
            "contact_notes": "",
            "default_diet": self.diet.id,
            "dietary_notes": "",
            "meal_plan": MealPlan.FIXED,
            "allergy_declaration": "yes",
            "allergy_title": "Al·lèrgia als fruits secs",
            "allergy_details": "Evitar traces de fruits secs.",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Adjunta el document mèdic")

        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(reverse("cafeteria:student_edit", args=[self.student.id]), {
                "first_name": self.student.first_name,
                "last_name": self.student.last_name,
                "birth_date": "",
                "contact_email": "",
                "contact_phone": "",
                "contact_notes": "",
                "default_diet": self.diet.id,
                "dietary_notes": "",
                "meal_plan": MealPlan.FIXED,
                "allergy_declaration": "yes",
                "allergy_title": "Al·lèrgia als fruits secs",
                "allergy_details": "Evitar traces de fruits secs.",
                "allergy_document": SimpleUploadedFile("informe.pdf", b"%PDF-1.4 informe", content_type="application/pdf"),
            })
            self.assertEqual(response.status_code, 302)
            self.student.refresh_from_db()
            self.assertTrue(self.student.has_allergy)
            self.assertEqual(self.student.allergy_review_status, AllergyReviewStatus.PENDING)
            self.assertTrue(self.student.allergy_document.storage.exists(self.student.allergy_document.name))

    def test_student_form_is_minimal_localized_and_explains_validation_errors(self):
        form = TutorStudentForm(instance=self.student, actor=self.user)
        for field_name in (
            "contact_email", "contact_phone", "contact_notes", "dietary_notes",
            "health_consent", "parental_authority",
        ):
            self.assertNotIn(field_name, form.fields)
        self.assertEqual(form.fields["meal_plan"].label, "Modalitat de menjador")
        self.assertEqual(form.fields["allergy_title"].label, "Títol de l’al·lèrgia")
        with translation.override("es"):
            spanish_form = TutorStudentForm(instance=self.student, actor=self.user)
            self.assertEqual(spanish_form.fields["meal_plan"].label, "Modalidad de comedor")
            self.assertEqual(spanish_form.fields["allergy_title"].label, "Título de la alergia")

        self.client.force_login(self.user)
        response = self.client.post(reverse("cafeteria:student_edit", args=[self.student.id]), {
            "first_name": self.student.first_name,
            "last_name": self.student.last_name,
            "default_diet": self.diet.id,
            "meal_plan": MealPlan.FIXED,
            "allergy_declaration": "yes",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Revisa els camps marcats")
        self.assertContains(response, "No s'han desat els canvis")
        self.assertContains(response, "name=\"allergy_document\"")
        self.assertNotContains(response, "name=\"contact_email\"")

    def test_daily_report_and_email_text_highlight_pending_allergies(self):
        self.student.has_allergy = True
        self.student.allergy_title = "Al·lèrgia a l’ou"
        self.student.allergy_details = "No servir ou ni derivats."
        self.student.kitchen_instructions = "Evitar ou i derivats."
        self.student.allergy_review_status = AllergyReviewStatus.PENDING
        self.student.save()
        MealBooking.objects.create(student=self.student, date=self.today, diet=self.diet)
        admin = User.objects.create_superuser("allergy-admin@example.com", "allergy-admin@example.com", "correct-horse-battery-staple")
        self.client.force_login(admin)
        response = self.client.get(f"{reverse('cafeteria:daily_reports')}?date={self.today.isoformat()}")
        self.assertContains(response, "Atenció: al·lèrgies")
        self.assertNotContains(response, self.student.allergy_title)
        self.assertNotContains(response, self.student.allergy_details)
        self.assertContains(response, self.student.kitchen_instructions)
        text = build_daily_report_text(self.today)
        self.assertIn("ATENCIÓ — AL·LÈRGIES", text)
        self.assertNotIn(self.student.allergy_title, text)
        self.assertIn(self.student.kitchen_instructions, text)
        self.assertIn("PENDENT DE VALIDAR", text)

    def test_staff_can_review_allergy_and_family_document_stays_private(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.student.has_allergy = True
            self.student.allergy_title = "Al·lèrgia a la llet"
            self.student.allergy_details = "No servir lactis."
            self.student.allergy_review_status = AllergyReviewStatus.PENDING
            self.student.allergy_document.save("informe.pdf", SimpleUploadedFile("informe.pdf", b"%PDF-1.4 informe"), save=True)
            self.student.allergy_document_name = "informe.pdf"
            self.student.save(update_fields=["allergy_document_name", "updated_at"])

            admin = User.objects.create_superuser("reviewer@example.com", "reviewer@example.com", "correct-horse-battery-staple")
            self.client.force_login(admin)
            response = self.client.post(reverse("cafeteria:allergy_review", args=[self.student.id]), {
                "decision": "reject", "rejection_reason": "Cal un informe vigent signat.",
            })
            self.assertEqual(response.status_code, 302)
            self.student.refresh_from_db()
            self.assertEqual(self.student.allergy_review_status, AllergyReviewStatus.REJECTED)
            self.assertEqual(self.student.allergy_rejection_reason, "Cal un informe vigent signat.")

            self.client.force_login(self.user)
            self.assertEqual(
                self.client.get(reverse("cafeteria:allergy_document_download", args=[self.student.id])).status_code,
                200,
            )
            outsider = User.objects.create_user("outsider@example.com", "outsider@example.com", "correct-horse-battery-staple")
            self.client.force_login(outsider)
            self.assertEqual(
                self.client.get(reverse("cafeteria:allergy_document_download", args=[self.student.id])).status_code,
                403,
            )

    def test_new_tutor_invitation_requires_initial_family_setup(self):
        admin = User.objects.create_superuser("onboarding-admin@example.com", "onboarding-admin@example.com", "correct-horse-battery-staple")
        PortalSettings.objects.create(allow_family_student_creation=True)
        new_family = Family.objects.create(name="Família Rius")
        invitation = Invitation.objects.create(email="onboarding@example.com", role="tutor", family=new_family, created_by=admin)
        response = self.client.post(reverse("cafeteria:invitation_accept", args=[invitation.token]), {
            "first_name": "Joana",
            "last_name": "Rius",
            "new_password1": "another-correct-horse-battery-staple",
            "new_password2": "another-correct-horse-battery-staple",
        })
        self.assertRedirects(response, reverse("cafeteria:family_onboarding", args=[new_family.id]))
        response = self.client.post(reverse("cafeteria:family_onboarding", args=[new_family.id]), {
            "family-phone": "600000000",
            "family-monthly_email_enabled": "on",
            "new-students-TOTAL_FORMS": "1",
            "new-students-INITIAL_FORMS": "0",
            "new-students-MIN_NUM_FORMS": "1",
            "new-students-MAX_NUM_FORMS": "1000",
            "new-students-0-course_group": self.group.id,
            "new-students-0-first_name": "Pau",
            "new-students-0-last_name": "Rius",
            "new-students-0-birth_date": "2020-01-15",
            "new-students-0-contact_email": "",
            "new-students-0-contact_phone": "",
            "new-students-0-contact_notes": "",
            "new-students-0-default_diet": self.diet.id,
            "new-students-0-dietary_notes": "",
            "new-students-0-meal_plan": MealPlan.FIXED,
            "new-students-0-allergy_declaration": "no",
            "new-students-0-allergy_title": "",
            "new-students-0-allergy_details": "",
        })
        self.assertRedirects(response, reverse("cafeteria:family_home"))
        membership = FamilyMembership.objects.get(family=new_family, user__email="onboarding@example.com")
        self.assertIsNotNone(membership.onboarding_completed_at)
        student = Student.objects.get(family=new_family, first_name="Pau")
        self.assertEqual(student.course_group, self.group)
        self.assertFalse(student.has_allergy)

    def test_second_tutor_skips_initial_setup_when_the_family_already_has_students(self):
        admin = User.objects.create_superuser("second-tutor-admin@example.com", "second-tutor-admin@example.com", "correct-horse-battery-staple")
        PortalSettings.objects.create(allow_family_student_creation=True)
        invitation = Invitation.objects.create(email="second-tutor@example.com", role="tutor", family=self.family, created_by=admin)
        response = self.client.post(reverse("cafeteria:invitation_accept", args=[invitation.token]), {
            "first_name": "Pol", "last_name": "Puig",
            "new_password1": "another-correct-horse-battery-staple",
            "new_password2": "another-correct-horse-battery-staple",
        })
        self.assertRedirects(response, reverse("cafeteria:dashboard"))
        membership = FamilyMembership.objects.get(family=self.family, user__email="second-tutor@example.com")
        self.assertFalse(membership.onboarding_required)
        self.assertIsNotNone(membership.onboarding_completed_at)

    def test_family_can_add_a_student_only_when_self_service_is_enabled(self):
        self.client.force_login(self.user)
        disabled = self.client.get(reverse("cafeteria:family_student_create", args=[self.family.id]))
        self.assertEqual(disabled.status_code, 404)
        PortalSettings.objects.create(allow_family_student_creation=True)
        response = self.client.post(reverse("cafeteria:family_student_create", args=[self.family.id]), {
            "course_group": self.group.id,
            "first_name": "Biel", "last_name": "Puig", "birth_date": "2021-02-03",
            "contact_email": "", "contact_phone": "", "contact_notes": "",
            "default_diet": self.diet.id, "dietary_notes": "", "meal_plan": MealPlan.SPORADIC,
            "allergy_declaration": "no", "allergy_title": "", "allergy_details": "",
        })
        self.assertRedirects(response, reverse("cafeteria:family_profile", args=[self.family.id]))
        student = Student.objects.get(family=self.family, first_name="Biel")
        self.assertEqual(student.course_group, self.group)
        self.assertFalse(student.is_scholarship)

    def test_portal_administration_requires_an_active_group_before_enabling_family_student_creation(self):
        admin = User.objects.create_superuser("family-settings@example.com", "family-settings@example.com", "correct-horse-battery-staple")
        self.client.force_login(admin)
        self.group.delete()
        response = self.client.post(f"{reverse('cafeteria:portal_administration')}?tab=configuracio", {
            "tab": "configuracio",
            "family-registration-allow_family_student_creation": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "primer crea un curs acadèmic actiu")
        self.assertFalse(PortalSettings.objects.get().allow_family_student_creation)

        group = CourseGroup.objects.create(academic_year=self.year, name="I5")
        response = self.client.post(f"{reverse('cafeteria:portal_administration')}?tab=configuracio", {
            "tab": "configuracio",
            "family-registration-allow_family_student_creation": "on",
        })
        self.assertRedirects(response, f"{reverse('cafeteria:portal_administration')}?tab=configuracio")
        self.assertTrue(PortalSettings.objects.get().allow_family_student_creation)
        self.assertEqual(group.academic_year, self.year)

    def test_web_app_manifest_and_service_worker_are_available(self):
        response = self.client.get(reverse("web_app_manifest"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        manifest = response.json()
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], reverse("cafeteria:dashboard"))
        self.assertEqual(
            manifest["icons"],
            [
                {
                    "src": static("cafeteria/images/pwa-logo-escola-192.png"),
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": static("cafeteria/images/pwa-logo-escola-512.png"),
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
            ],
        )
        response = self.client.get(reverse("web_app_service_worker"))
        self.assertEqual(response.status_code, 200)
        script = response.content.decode()
        self.assertIn("/static/", script)
        self.assertRegex(script, r"afa-ordis-static-[0-9a-f]{16}")

    def test_monthly_booking_api_reserves_cancels_and_changes_one_diet(self):
        alternative_diet = Diet.objects.create(name="Vegetariana")
        self.client.force_login(self.user)
        update_url = reverse("cafeteria:family_booking_update", args=[self.family.id])
        response = self.client.post(update_url, {
            "student_id": self.student.id, "service_date": self.today.isoformat(), "operation": "reserve",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["booking"]["state"], "reserved")
        booking = MealBooking.objects.get(student=self.student, date=self.today)
        self.assertEqual(booking.diet, self.diet)

        response = self.client.post(update_url, {
            "student_id": self.student.id, "service_date": self.today.isoformat(), "operation": "diet",
            "diet_id": alternative_diet.id,
        })
        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.diet, alternative_diet)

        response = self.client.post(update_url, {
            "student_id": self.student.id, "service_date": self.today.isoformat(), "operation": "cancel",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["booking"]["state"], "empty")
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CANCELLED)

    def test_monthly_booking_apply_uses_each_sibling_default_diet(self):
        sibling_diet = Diet.objects.create(name="Sense gluten")
        sibling = Student.objects.create(
            family=self.family, course_group=self.group, first_name="Biel", last_name="Puig",
            default_diet=sibling_diet, meal_plan=MealPlan.FIXED,
        )
        self.client.force_login(self.user)
        response = self.client.post(reverse("cafeteria:family_booking_apply", args=[self.family.id]), {
            "service_date": self.today.isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 2)
        self.assertEqual(MealBooking.objects.get(student=self.student, date=self.today).diet, self.diet)
        booking = MealBooking.objects.get(student=sibling, date=self.today)
        self.assertEqual(booking.diet, sibling_diet)

        response = self.client.post(reverse("cafeteria:family_booking_apply", args=[self.family.id]), {
            "service_date": self.today.isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["action"], "cancel")
        self.assertFalse(MealBooking.objects.filter(
            student__in=[self.student, sibling], date=self.today, status=BookingStatus.ACTIVE,
        ).exists())

    def test_monthly_booking_api_does_not_bypass_the_cutoff(self):
        MealSettings.objects.create(academic_year=self.year, daily_cutoff=time(0, 0))
        self.client.force_login(self.user)
        response = self.client.post(reverse("cafeteria:family_booking_update", args=[self.family.id]), {
            "student_id": self.student.id, "service_date": self.today.isoformat(), "operation": "reserve",
        })
        self.assertEqual(response.status_code, 409)
        self.assertFalse(MealBooking.objects.filter(student=self.student, date=self.today, status=BookingStatus.ACTIVE).exists())

    def test_tutor_cannot_change_scholarship_status(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("cafeteria:student_edit", args=[self.student.id]), {
            "first_name": "Núria",
            "last_name": "Puig",
            "course_group": self.group.id,
            "default_diet": self.diet.id,
            "meal_plan": MealPlan.SPORADIC,
            "is_scholarship": "on",
            "active": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.student.refresh_from_db()
        self.assertFalse(self.student.is_scholarship)
        self.assertEqual(self.student.meal_plan, MealPlan.SPORADIC)

    def test_course_closure_keeps_existing_booking(self):
        booking = MealBooking.objects.create(student=self.student, date=self.today, diet=self.diet)
        CourseClosure.objects.create(course_group=self.group, date=self.today, title="Excursió")
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.ACTIVE)
        self.assertEqual(booking.diet, self.diet)

    def test_academic_holiday_cancels_bookings_and_closes_service(self):
        student_booking = MealBooking.objects.create(student=self.student, date=self.today, diet=self.diet)
        teacher_user = User.objects.create_user("teacher-holiday@example.com", "teacher-holiday@example.com", "correct-horse-battery-staple")
        teacher = TeacherMealProfile.objects.create(user=teacher_user, default_diet=self.diet)
        teacher_booking = TeacherMealBooking.objects.create(teacher=teacher, date=self.today, diet=self.diet)

        holiday = AcademicHoliday.objects.create(
            academic_year=self.year,
            title="Festiu local",
            holiday_type="local",
            starts_on=self.today,
            ends_on=self.today,
        )

        student_booking.refresh_from_db()
        teacher_booking.refresh_from_db()
        self.assertEqual(student_booking.status, BookingStatus.CANCELLED)
        self.assertEqual(teacher_booking.status, BookingStatus.CANCELLED)
        self.assertFalse(is_service_day(self.today, self.student))
        admin = User.objects.create_superuser("calendar@example.com", "calendar@example.com", "correct-horse-battery-staple")
        self.client.force_login(admin)
        response = self.client.get(f"{reverse('cafeteria:school_calendar')}?year={self.year.id}&month={self.today:%Y-%m}")
        self.assertContains(response, holiday.title)

    def test_intensive_period_is_visible_but_does_not_change_service_or_booking(self):
        booking = MealBooking.objects.create(student=self.student, date=self.today, diet=self.diet)
        period = AcademicIntensivePeriod.objects.create(
            academic_year=self.year,
            title="Horari intensiu de juny",
            starts_on=self.today,
            ends_on=self.today,
        )
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.ACTIVE)
        self.assertTrue(is_service_day(self.today, self.student))

        self.client.force_login(self.user)
        response = self.client.get(reverse("cafeteria:family_school_calendar", args=[self.family.id]))
        self.assertContains(response, period.title)

    def test_intensive_period_must_stay_inside_its_academic_year(self):
        period = AcademicIntensivePeriod(
            academic_year=self.year,
            title="Fora del curs",
            starts_on=self.year.starts_on - timedelta(days=1),
            ends_on=self.year.starts_on,
        )
        with self.assertRaises(ValidationError):
            period.full_clean()

    def test_calendar_notice_is_visible_to_family_without_changing_meal_service(self):
        booking = MealBooking.objects.create(student=self.student, date=self.today, diet=self.diet)
        notice = AcademicNotice.objects.create(
            academic_year=self.year,
            title="Vaga del personal docent",
            description="L'activitat lectiva continua amb serveis mínims.",
            level="alert",
            starts_on=self.today,
            ends_on=self.today,
        )
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.ACTIVE)
        self.assertTrue(is_service_day(self.today, self.student))
        self.client.force_login(self.user)
        response = self.client.get(reverse("cafeteria:family_school_calendar", args=[self.family.id]))
        self.assertContains(response, notice.title)

    def test_family_calendar_filters_excursions_to_child_groups_by_default(self):
        other_group = CourseGroup.objects.create(academic_year=self.year, name="I5")
        other_family = Family.objects.create(name="Família Serra")
        Student.objects.create(
            family=other_family, course_group=other_group, first_name="Berta", last_name="Serra",
            default_diet=self.diet,
        )
        own_closure = CourseClosure.objects.create(course_group=self.group, date=self.today, title="Excursió I4")
        CourseClosure.objects.create(course_group=other_group, date=self.today, title="Excursió I5")
        self.client.force_login(self.user)
        response = self.client.get(reverse("cafeteria:family_school_calendar", args=[self.family.id]))
        self.assertContains(response, own_closure.title)
        self.assertNotContains(response, "Excursió I5")

    def test_family_contact_edit_does_not_allow_group_or_scholarship_changes(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("cafeteria:family_profile", args=[self.family.id]), {
            "phone": "600111222",
            "monthly_email_enabled": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.family.refresh_from_db()
        self.assertEqual(self.family.phone, "600111222")

        other_group = CourseGroup.objects.create(academic_year=self.year, name="I5")
        response = self.client.post(reverse("cafeteria:student_edit", args=[self.student.id]), {
            "first_name": "Núria", "last_name": "Puig", "birth_date": "",
            "contact_email": "", "contact_phone": "", "contact_notes": "",
            "default_diet": self.diet.id, "dietary_notes": "", "meal_plan": MealPlan.SPORADIC,
            "course_group": other_group.id, "is_scholarship": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.student.refresh_from_db()
        self.assertEqual(self.student.course_group, self.group)
        self.assertFalse(self.student.is_scholarship)
        self.assertEqual(self.student.meal_plan, MealPlan.SPORADIC)

    def test_family_context_only_accepts_linked_families(self):
        second_family = Family.objects.create(name="Segona família")
        FamilyMembership.objects.create(family=second_family, user=self.user)
        other_family = Family.objects.create(name="Família no vinculada")
        self.client.force_login(self.user)
        response = self.client.post(reverse("cafeteria:family_context_select"), {"family_id": second_family.id})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["cafeteria_active_family_id"], second_family.id)
        self.assertEqual(
            self.client.post(reverse("cafeteria:family_context_select"), {"family_id": other_family.id}).status_code,
            404,
        )

    def test_afa_membership_is_optional_and_manually_recorded(self):
        admin = User.objects.create_superuser("afa@example.com", "afa@example.com", "correct-horse-battery-staple")
        self.client.force_login(admin)
        response = self.client.post(
            f"{reverse('cafeteria:afa_membership_edit', args=[self.family.id])}?year={self.year.id}",
            {
                "status": AfaMembershipStatus.PAID,
                "amount": "25.00",
                "paid_on": self.today.isoformat(),
                "payment_method": "transfer",
                "payment_reference": "TRX-2026",
                "notes": "Quota rebuda",
            },
        )
        self.assertEqual(response.status_code, 302)
        membership = AfaMembership.objects.get(family=self.family, academic_year=self.year)
        self.assertEqual(membership.amount, Decimal("25.00"))
        self.assertEqual(membership.status, AfaMembershipStatus.PAID)
        self.assertTrue(AfaFeeSettings.objects.filter(academic_year=self.year).exists())

        membership.delete()
        self.client.force_login(self.user)
        response = self.client.post(reverse("cafeteria:bulk_booking", args=[self.family.id]), {
            "student_id": self.student.id,
            "dates": [self.today.isoformat()],
            "action": "add",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(MealBooking.objects.filter(student=self.student, date=self.today, status=BookingStatus.ACTIVE).exists())

    def test_monthly_statement_uses_booked_price(self):
        MealBooking.objects.create(student=self.student, date=self.today, diet=self.diet)
        statement = prepare_monthly_statement(self.family, self.today.year, self.today.month)
        self.assertEqual(statement.total, Decimal("6.50"))
        self.assertEqual(statement.lines.count(), 1)

    def test_daily_report_is_emailed_to_configured_recipients(self):
        MealBooking.objects.create(student=self.student, date=self.today, diet=self.diet)
        meal_settings = MealSettings.objects.create(academic_year=self.year, daily_cutoff="09:00", daily_reports_enabled=True)
        kitchen = User.objects.create_user("cuina@example.com", "cuina@example.com", "correct-horse-battery-staple")
        kitchen.groups.add(Group.objects.get_or_create(name="kitchen")[0])
        DailyReportRecipient.objects.create(settings=meal_settings, email=kitchen.email, user=kitchen)
        self.assertTrue(send_daily_report(self.today.isoformat()))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["cuina@example.com"])

    def test_report_schedule_is_independent_from_family_cutoff(self):
        meal_settings = MealSettings.objects.create(
            academic_year=self.year,
            daily_cutoff=time(9, 0),
            daily_report_send_time=time(11, 30),
            daily_reports_enabled=True,
        )
        DailyReportRecipient.objects.create(settings=meal_settings, email="cuina@example.com")
        at_ten = timezone.make_aware(datetime.combine(self.today, time(10, 0)))
        at_noon = timezone.make_aware(datetime.combine(self.today, time(12, 0)))
        self.assertFalse(expected_report_is_due(meal_settings, at_ten))
        self.assertTrue(expected_report_is_due(meal_settings, at_noon))

    def test_family_calendar_shows_remaining_time_before_today_cutoff(self):
        now = timezone.localtime()
        if now.hour == 23:
            self.skipTest("No hi ha prou marge dins del dia per comprovar el compte enrere.")
        MealSettings.objects.create(
            academic_year=self.year,
            daily_cutoff=(now + timedelta(hours=1)).time().replace(second=0, microsecond=0),
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("cafeteria:family_calendar", args=[self.family.id]))
        self.assertContains(response, "Canvis d'avui")
        self.assertContains(response, "Encara pots modificar la reserva d'avui")

    @override_settings(EMAIL_HOST="mail.example.test")
    def test_password_reset_uses_the_namespaced_portal_url(self):
        response = self.client.post(reverse("cafeteria:password_reset"), {"email": self.user.email})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/ca/comptes/contrasenya/", mail.outbox[0].body)

    @override_settings(EMAIL_HOST="mail.example.test")
    def test_account_email_is_case_insensitive_for_login_and_password_reset(self):
        account = User.objects.create_user(
            "legacy-account-name", "Case.Login@Example.COM", "correct-horse-battery-staple",
        )

        response = self.client.post(reverse("cafeteria:login"), {
            "username": "CASE.LOGIN@example.com",
            "password": "correct-horse-battery-staple",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), account.pk)

        self.client.post(reverse("cafeteria:password_reset"), {"email": "case.login@EXAMPLE.com"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [account.email])

    def test_account_email_cannot_be_reused_with_different_casing(self):
        User.objects.create_user("first-account", "case-unique@example.com", "correct-horse-battery-staple")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user("second-account", "CASE-UNIQUE@example.com", "correct-horse-battery-staple")

    def test_report_recipient_email_cannot_be_reused_with_different_casing(self):
        meal_settings = MealSettings.objects.create(academic_year=self.year)
        DailyReportRecipient.objects.create(settings=meal_settings, email="Kitchen@Example.COM")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DailyReportRecipient.objects.create(settings=meal_settings, email="KITCHEN@example.com")

    def test_invitation_email_is_canonicalized(self):
        invitation = Invitation.objects.create(email="New.Tutor@Example.COM", role="teacher")
        self.assertEqual(invitation.email, "new.tutor@example.com")

    @override_settings(EMAIL_HOST="", DEBUG=True)
    def test_administration_can_copy_a_reset_link_without_smtp(self):
        admin = User.objects.create_superuser("reset@example.com", "reset@example.com", "correct-horse-battery-staple")
        self.client.force_login(admin)
        response = self.client.post(reverse("cafeteria:account_reset_link", args=[self.user.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/comptes/contrasenya/")

    def test_teacher_invitation_creates_profile_booking_and_statement(self):
        admin = User.objects.create_superuser("teacher-admin@example.com", "teacher-admin@example.com", "correct-horse-battery-staple")
        invitation = Invitation.objects.create(email="teacher@example.com", role="teacher", created_by=admin)
        self.client.post(reverse("cafeteria:invitation_accept", args=[invitation.token]), {
            "first_name": "Alex", "last_name": "Ferrer",
            "new_password1": "another-correct-horse-battery-staple",
            "new_password2": "another-correct-horse-battery-staple",
        })
        profile = TeacherMealProfile.objects.get(user__email="teacher@example.com")
        PriceRule.objects.create(scholarship=False, meal_plan=MealPlan.SPORADIC, effective_from=self.today - timedelta(days=1), amount=Decimal("7.00"))
        response = self.client.get(f"{reverse('cafeteria:teacher_calendar')}?month={self.today:%Y-%m}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Calendari mensual de reserves")
        self.assertNotContains(response, "Setmana del")
        response = self.client.get(f"{reverse('cafeteria:teacher_calendar')}?week={self.today:%Y-%m-%d}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["month_start"], self.today.replace(day=1))

        response = self.client.post(reverse("cafeteria:teacher_booking_update"), {
            "service_date": self.today.isoformat(), "operation": "reserve",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["booking"]["state"], "reserved")
        booking = TeacherMealBooking.objects.get(teacher=profile, date=self.today)
        self.assertEqual(booking.unit_price, Decimal("7.00"))

        alternative_diet = Diet.objects.create(name="Vegetariana docent")
        response = self.client.post(reverse("cafeteria:teacher_booking_update"), {
            "service_date": self.today.isoformat(), "operation": "diet", "diet_id": alternative_diet.id,
        })
        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.diet, alternative_diet)

        response = self.client.post(reverse("cafeteria:teacher_booking_update"), {
            "service_date": self.today.isoformat(), "operation": "cancel",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["booking"]["state"], "empty")
        statement = prepare_teacher_monthly_statement(profile, self.today.year, self.today.month)
        self.assertEqual(statement.total, Decimal("0.00"))

    def test_public_django_admin_route_is_disabled(self):
        self.assertEqual(self.client.get("/ca/admin/").status_code, 404)

    def test_csv_import_is_previewed_then_atomically_confirmed(self):
        admin = User.objects.create_superuser("admin@example.com", "admin@example.com", "correct-horse-battery-staple")
        self.client.force_login(admin)
        csv_content = (
            "family_name,family_phone,student_first_name,student_last_name,"
            "birth_date,course_group,student_email,student_phone,contact_notes,default_diet,dietary_notes,scholarship,meal_plan\n"
            f"Família Nova,600000000,Arnau,Serra,2019-02-03,{self.group.name},,,,Ordinària,,No,Fix\n"
        )
        response = self.client.post(reverse("cafeteria:family_import"), {
            "academic_year": self.year.id,
            "csv_file": SimpleUploadedFile("families.csv", csv_content.encode(), content_type="text/csv"),
        })
        self.assertEqual(response.status_code, 302)
        batch = FamilyImportBatch.objects.get()
        self.assertEqual(batch.valid_rows[0]["student_first_name"], "Arnau")
        response = self.client.post(reverse("cafeteria:family_import_confirm", args=[batch.id]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Student.objects.filter(first_name="Arnau", last_name="Serra").exists())
        batch.refresh_from_db()
        self.assertEqual(batch.status, FamilyImportBatch.Status.IMPORTED)
        self.assertEqual(batch.valid_rows, [])

    def test_custom_management_screens_render_for_superuser(self):
        admin = User.objects.create_superuser("admin2@example.com", "admin2@example.com", "correct-horse-battery-staple")
        self.client.force_login(admin)
        for route in (
            "cafeteria:management_dashboard",
            "cafeteria:dining_dashboard",
            "cafeteria:contacts_dashboard",
            "cafeteria:academic_dashboard",
            "cafeteria:intensive_period_create",
            "cafeteria:people",
            "cafeteria:afa_memberships",
            "cafeteria:school_calendar",
            "cafeteria:meal_configuration",
            "cafeteria:course_management",
            "cafeteria:portal_administration",
            "cafeteria:family_import",
        ):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(reverse(route)).status_code, 200)

    def test_administrator_can_save_navigation_and_toggle_a_day_in_annual_calendar(self):
        admin = User.objects.create_superuser("calendar-actions@example.com", "calendar-actions@example.com", "correct-horse-battery-staple")
        self.client.force_login(admin)
        response = self.client.post(reverse("cafeteria:navigation_preferences"), {"section": "calendari", "collapsed": "1"})
        self.assertEqual(response.status_code, 204)
        admin.profile.refresh_from_db()
        self.assertTrue(admin.profile.navigation_state["calendari"]["collapsed"])
        response = self.client.post(
            reverse("cafeteria:service_day_by_date_toggle", args=[self.year.id, self.today.isoformat()]),
            {"is_service_day": "0"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ServiceDay.objects.get(academic_year=self.year, date=self.today).is_service_day)

    def test_manager_can_use_dining_area_but_not_contacts_or_academic_area(self):
        manager = User.objects.create_user("manager@example.com", "manager@example.com", "correct-horse-battery-staple")
        manager.groups.create(name="manager")
        self.client.force_login(manager)
        self.assertEqual(self.client.get(reverse("cafeteria:dining_dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("cafeteria:contacts_dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("cafeteria:academic_dashboard")).status_code, 403)

    def test_first_academic_year_can_be_created_from_the_calendar(self):
        AcademicYear.objects.all().delete()
        admin = User.objects.create_superuser("first-year@example.com", "first-year@example.com", "correct-horse-battery-staple")
        self.client.force_login(admin)
        calendar_url = reverse("cafeteria:school_calendar")
        response = self.client.get(calendar_url)
        self.assertContains(response, "Crea el primer curs acadèmic")
        response = self.client.post(reverse("cafeteria:academic_year_create"), {
            "name": "2026-2027", "starts_on": "2026-09-01", "ends_on": "2027-06-30", "is_active": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AcademicYear.objects.filter(name="2026-2027", is_active=True).exists())

    def test_admin_can_edit_an_academic_year_and_reconcile_dates_outside_the_new_period(self):
        admin = User.objects.create_superuser("calendar-edit@example.com", "calendar-edit@example.com", "correct-horse-battery-staple")
        later_date = self.today + timedelta(days=20)
        new_end = self.today + timedelta(days=5)
        ServiceDay.objects.create(academic_year=self.year, date=later_date, is_service_day=True)
        booking = MealBooking.objects.create(student=self.student, date=later_date, diet=self.diet)
        closure = CourseClosure.objects.create(course_group=self.group, date=later_date, title="Excursió tardana")
        intensive_period = AcademicIntensivePeriod.objects.create(
            academic_year=self.year, title="Intensiva tardana", starts_on=later_date, ends_on=later_date,
        )
        self.client.force_login(admin)

        response = self.client.get(reverse("cafeteria:academic_year_edit", args=[self.year.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edita el curs acadèmic")

        response = self.client.post(reverse("cafeteria:academic_year_edit", args=[self.year.id]), {
            "name": self.year.name,
            "starts_on": self.year.starts_on.isoformat(),
            "ends_on": new_end.isoformat(),
            "is_active": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.year.refresh_from_db()
        booking.refresh_from_db()
        self.assertEqual(self.year.ends_on, new_end)
        self.assertFalse(ServiceDay.objects.filter(academic_year=self.year, date=later_date).exists())
        self.assertEqual(booking.status, BookingStatus.CANCELLED)
        self.assertFalse(CourseClosure.objects.filter(pk=closure.pk).exists())
        self.assertFalse(AcademicIntensivePeriod.objects.filter(pk=intensive_period.pk).exists())

    def test_admin_can_edit_and_delete_a_course_group_without_deleting_students(self):
        admin = User.objects.create_superuser("group-edit@example.com", "group-edit@example.com", "correct-horse-battery-staple")
        booking = MealBooking.objects.create(student=self.student, date=self.today, diet=self.diet)
        closure = CourseClosure.objects.create(course_group=self.group, date=self.today, title="Excursió I4")
        booking.refresh_from_db()
        self.assertEqual(booking.diet, self.diet)
        self.client.force_login(admin)

        response = self.client.get(reverse("cafeteria:course_group_edit", args=[self.group.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edita el grup")
        response = self.client.post(reverse("cafeteria:course_group_edit", args=[self.group.id]), {
            "name": "I4 A",
            "sort_order": "3",
        })
        self.assertEqual(response.status_code, 302)
        self.group.refresh_from_db()
        self.assertEqual(self.group.name, "I4 A")
        self.assertEqual(self.group.sort_order, 3)

        response = self.client.post(reverse("cafeteria:course_group_delete", args=[self.group.id]))
        self.assertEqual(response.status_code, 302)
        self.student.refresh_from_db()
        self.assertIsNone(self.student.course_group)
        self.assertFalse(CourseGroup.objects.filter(pk=self.group.pk).exists())
        self.assertFalse(CourseClosure.objects.filter(pk=closure.pk).exists())
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.ACTIVE)

    def test_admin_can_edit_and_delete_a_diet_without_breaking_profiles_or_history(self):
        admin = User.objects.create_superuser("diet-edit@example.com", "diet-edit@example.com", "correct-horse-battery-staple")
        teacher_user = User.objects.create_user("teacher-diet@example.com", "teacher-diet@example.com", "correct-horse-battery-staple")
        teacher = TeacherMealProfile.objects.create(user=teacher_user, default_diet=self.diet)
        booking = MealBooking.objects.create(student=self.student, date=self.today, diet=self.diet)
        original_diet_name = booking.diet_name
        self.client.force_login(admin)

        response = self.client.get(reverse("cafeteria:diet_edit", args=[self.diet.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edita la dieta")
        response = self.client.post(reverse("cafeteria:diet_edit", args=[self.diet.id]), {
            "name": "Vegetariana",
            "description": "Sense carn ni peix",
            "active": "on",
            "sort_order": "2",
        })
        self.assertEqual(response.status_code, 302)
        self.diet.refresh_from_db()
        self.assertEqual(self.diet.name, "Vegetariana")
        self.assertEqual(self.diet.sort_order, 2)

        response = self.client.post(reverse("cafeteria:diet_delete", args=[self.diet.id]))
        self.assertEqual(response.status_code, 302)
        self.student.refresh_from_db()
        teacher.refresh_from_db()
        booking.refresh_from_db()
        self.assertFalse(Diet.objects.filter(pk=self.diet.pk).exists())
        self.assertNotEqual(self.student.default_diet_id, self.diet.pk)
        self.assertNotEqual(teacher.default_diet_id, self.diet.pk)
        self.assertTrue(self.student.default_diet.active)
        self.assertIsNone(booking.diet)
        self.assertEqual(booking.diet_name, original_diet_name)

    def test_member_dashboard_and_responsive_booking_view_render(self):
        self.client.force_login(self.user)
        with translation.override("ca"):
            self.assertEqual(self.client.get(reverse("cafeteria:dashboard")).status_code, 200)
            self.assertEqual(self.client.get(reverse("cafeteria:family_home")).status_code, 200)
            self.assertEqual(self.client.get(reverse("cafeteria:family_calendar", args=[self.family.id])).status_code, 200)
            self.assertEqual(self.client.get(reverse("cafeteria:family_profile", args=[self.family.id])).status_code, 200)
            self.assertEqual(self.client.get(reverse("cafeteria:family_school_calendar", args=[self.family.id])).status_code, 200)

    def test_language_choice_is_saved_on_the_profile(self):
        self.client.force_login(self.user)
        with translation.override("ca"):
            response = self.client.post(reverse("cafeteria:set_language"), {"language": "es", "next": reverse("cafeteria:dashboard")})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/es/")
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "es")
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.language, "es")

    def test_catalan_is_the_default_even_with_a_spanish_browser(self):
        response = self.client.get("/", HTTP_ACCEPT_LANGUAGE="es-ES,es;q=0.9")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/ca/")

        response = self.client.get("/ca/comptes/entrada/", HTTP_ACCEPT_LANGUAGE="es-ES,es;q=0.9")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Language"], "ca")
        self.assertContains(response, 'lang="ca"')
        self.assertContains(response, "Benvinguda")

    def test_switching_language_keeps_the_same_internal_page_and_query(self):
        self.client.force_login(self.user)
        spanish_path = f"/es/families/{self.family.id}/menjador/?month=2026-09"
        response = self.client.post("/es/comptes/idioma/", {"language": "ca", "next": spanish_path})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/ca/families/{self.family.id}/menjador/?month=2026-09")
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "ca")
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.language, "ca")

    def test_anonymous_language_choice_is_remembered_in_a_cookie(self):
        response = self.client.post("/ca/comptes/idioma/", {
            "language": "es",
            "next": "/ca/comptes/entrada/",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/es/comptes/entrada/")
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "es")

        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "es"
        response = self.client.get("/es/comptes/entrada/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Language"], "es")
        self.assertContains(response, "Bienvenida")
        self.assertContains(response, "reservas de comedor vinculadas a tu cuenta")

    def test_language_choice_does_not_redirect_to_an_external_url(self):
        response = self.client.post("/ca/comptes/idioma/", {
            "language": "es",
            "next": "https://example.invalid/",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/es/")

    @override_settings(EMAIL_HOST="", APP_BASE_URL="")
    def test_invitation_link_can_be_created_without_smtp(self):
        admin = User.objects.create_superuser("links@example.com", "links@example.com", "correct-horse-battery-staple")
        self.client.force_login(admin)
        with translation.override("ca"):
            response = self.client.post(reverse("cafeteria:invitation_create"), {
                "email": "new-tutor@example.com", "role": "tutor", "family": self.family.id,
            })
        self.assertEqual(response.status_code, 200)
        invitation = Invitation.objects.get(email="new-tutor@example.com")
        self.assertContains(response, invitation.token)
        self.assertContains(response, "Obre l'enllaç")
        self.assertEqual(len(mail.outbox), 0)
        self.client.post(reverse("cafeteria:invitation_accept", args=[invitation.token]), {
            "first_name": "Joana", "last_name": "Rius",
            "new_password1": "another-correct-horse-battery-staple",
            "new_password2": "another-correct-horse-battery-staple",
        })
        invited_user = User.objects.get(email="new-tutor@example.com")
        self.assertTrue(invited_user.check_password("another-correct-horse-battery-staple"))
        self.assertTrue(FamilyMembership.objects.filter(user=invited_user, family=self.family).exists())

    def test_portal_templates_compile(self):
        templates = (
            "cafeteria/price_rules.html", "cafeteria/daily_reports.html", "cafeteria/monthly_statements.html",
            "cafeteria/statement_detail.html", "cafeteria/invitation_form.html", "cafeteria/student_form.html",
            "cafeteria/audit_log.html", "cafeteria/family_import_preview.html", "cafeteria/family_home.html",
            "cafeteria/family_profile.html", "cafeteria/family_onboarding.html", "cafeteria/family_school_calendar.html",
            "cafeteria/allergy_review_queue.html", "cafeteria/allergy_review.html",
            "cafeteria/economic_dashboard.html", "cafeteria/economic_entries.html",
            "cafeteria/economic_reports.html",
            "cafeteria/economic_entry_form.html", "cafeteria/economic_review.html",
            "cafeteria/economic_configuration.html", "cafeteria/economic_my_expenses.html",
            "cafeteria/economic_submission_form.html",
        )
        for template in templates:
            with self.subTest(template=template):
                get_template(template)


class PortalBackupTests(TransactionTestCase):
    def unpack(self, payload):
        from django.conf import settings
        if settings.DATA_ENCRYPTION_ENABLED:
            from .crypto import decrypt_stream
            clear = BytesIO()
            decrypt_stream(BytesIO(payload), clear, purpose="backup")
            return clear.getvalue()
        return payload

    def test_administrator_can_download_a_complete_zip_backup_from_the_portal(self):
        admin = User.objects.create_superuser("backup@example.com", "backup@example.com", "correct-horse-battery-staple")
        self.client.force_login(admin)
        response = self.client.post(reverse("cafeteria:portal_backup_download"))
        self.assertEqual(response.status_code, 200)
        from django.conf import settings
        self.assertEqual(response["Content-Type"], "application/octet-stream" if settings.DATA_ENCRYPTION_ENABLED else "application/zip")
        self.assertIn("attachment; filename=", response["Content-Disposition"])
        payload = b"".join(response.streaming_content)
        response.close()
        self.assertGreater(len(payload), 0)
        with zipfile.ZipFile(BytesIO(self.unpack(payload))) as archive:
            self.assertIn("backup.json", archive.namelist())
            self.assertIn("database.sqlite3", archive.namelist())

    def test_complete_backup_includes_uploaded_receipts(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            admin = User.objects.create_superuser("backup-files@example.com", "backup-files@example.com", "correct-horse-battery-staple")
            account, _created = FinancialAccount.objects.get_or_create(name="Compte bancari AFA", defaults={"account_type": "bank"})
            category, _created = EconomicCategory.objects.get_or_create(name="Material", entry_type=EconomicEntryType.EXPENSE)
            entry = EconomicEntry.objects.create(
                entry_type=EconomicEntryType.EXPENSE, date=timezone.localdate(), concept="Document", category=category,
                account=account, amount=Decimal("1.00"), review_status=EconomicReviewStatus.APPROVED,
                payment_status=EconomicPaymentStatus.PAID, paid_on=timezone.localdate(), submitted_by=admin,
            )
            attachment = EconomicAttachment.objects.create(entry=entry, file=SimpleUploadedFile("ticket.jpg", b"receipt"), original_name="ticket.jpg", uploaded_by=admin)
            self.client.force_login(admin)
            response = self.client.post(reverse("cafeteria:portal_backup_download"))
            payload = b"".join(response.streaming_content)
            response.close()
            with zipfile.ZipFile(BytesIO(self.unpack(payload))) as archive:
                self.assertIn(f"media/{attachment.file.name}", archive.namelist())


class EconomicFlowTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.admin = User.objects.create_superuser("admin-economy@example.com", "admin-economy@example.com", "correct-horse-battery-staple")
        self.submitter = User.objects.create_user("submitter@example.com", "submitter@example.com", "correct-horse-battery-staple")
        self.submitter.profile.can_submit_expenses = True
        self.submitter.profile.save(update_fields=["can_submit_expenses"])
        self.account = FinancialAccount.objects.get(name="Compte bancari AFA")
        self.expense_category = EconomicCategory.objects.get(name="Material", entry_type=EconomicEntryType.EXPENSE)
        self.income_category = EconomicCategory.objects.get(name="Donacions", entry_type=EconomicEntryType.INCOME)

    def tearDown(self):
        self.settings_override.disable()
        self.media.cleanup()

    def _receipt(self, name="ticket.jpg"):
        return SimpleUploadedFile(name, b"receipt", content_type="image/jpeg")

    def test_authorized_user_can_submit_a_receipted_expense(self):
        self.client.force_login(self.submitter)
        response = self.client.post(reverse("cafeteria:economic_submission_create"), {
            "date": "2026-09-04", "concept": "Cartolines", "category": self.expense_category.id,
            "amount": "12.50", "attachments": self._receipt(),
        })
        self.assertRedirects(response, reverse("cafeteria:economic_my_expenses"))
        entry = EconomicEntry.objects.get(concept="Cartolines")
        self.assertEqual(entry.entry_type, EconomicEntryType.EXPENSE)
        self.assertEqual(entry.review_status, EconomicReviewStatus.SUBMITTED)
        self.assertEqual(entry.payment_status, EconomicPaymentStatus.PENDING)
        self.assertEqual(entry.submitted_by, self.submitter)
        self.assertEqual(entry.attachments.count(), 1)

    def test_submission_requires_a_receipt_and_is_private(self):
        self.client.force_login(self.submitter)
        response = self.client.post(reverse("cafeteria:economic_submission_create"), {
            "date": "2026-09-04", "concept": "Sense document", "category": self.expense_category.id, "amount": "4.00",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Adjunta com a mínim un justificant")
        entry = EconomicEntry.objects.create(
            entry_type=EconomicEntryType.EXPENSE, date=timezone.localdate(), concept="Privada", category=self.expense_category,
            amount=Decimal("1.00"), review_status=EconomicReviewStatus.SUBMITTED, payment_status=EconomicPaymentStatus.PENDING,
            submitted_by=self.submitter,
        )
        attachment = EconomicAttachment.objects.create(entry=entry, file=self._receipt(), original_name="ticket.jpg", uploaded_by=self.submitter)
        other = User.objects.create_user("other@example.com", "other@example.com", "correct-horse-battery-staple")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("cafeteria:economic_attachment_download", args=[attachment.id])).status_code, 403)

    def test_administrator_approves_and_marks_a_submission_paid(self):
        entry = EconomicEntry.objects.create(
            entry_type=EconomicEntryType.EXPENSE, date=timezone.localdate(), concept="Pintura", category=self.expense_category,
            amount=Decimal("18.00"), review_status=EconomicReviewStatus.SUBMITTED,
            payment_status=EconomicPaymentStatus.PENDING, submitted_by=self.submitter,
        )
        EconomicAttachment.objects.create(entry=entry, file=self._receipt(), original_name="ticket.jpg", uploaded_by=self.submitter)
        self.client.force_login(self.admin)
        response = self.client.post(reverse("cafeteria:economic_review", args=[entry.id]), {
            "entry_type": EconomicEntryType.EXPENSE, "date": entry.date.isoformat(), "concept": entry.concept,
            "category": self.expense_category.id, "account": self.account.id, "amount": "18.00",
            "notes": "", "payment_status": EconomicPaymentStatus.PENDING, "paid_on": "", "action": "approve",
        })
        self.assertRedirects(response, reverse("cafeteria:economic_entries"))
        entry.refresh_from_db()
        self.assertEqual(entry.review_status, EconomicReviewStatus.APPROVED)
        self.assertEqual(entry.account, self.account)
        response = self.client.post(reverse("cafeteria:economic_mark_paid", args=[entry.id]), {"paid_on": "2026-09-05"})
        self.assertRedirects(response, reverse("cafeteria:economic_entries"))
        entry.refresh_from_db()
        self.assertEqual(entry.payment_status, EconomicPaymentStatus.PAID)
        self.assertEqual(entry.paid_on.isoformat(), "2026-09-05")

    def test_reports_include_paid_entries_and_csv(self):
        EconomicEntry.objects.create(
            entry_type=EconomicEntryType.INCOME, date=timezone.localdate(), concept="Donació", category=self.income_category,
            account=self.account, amount=Decimal("30.00"), review_status=EconomicReviewStatus.APPROVED,
            payment_status=EconomicPaymentStatus.PAID, paid_on=timezone.localdate(), submitted_by=self.admin,
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse("cafeteria:economic_reports"), {"period": "calendar", "year": timezone.localdate().year})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Donacions")
        response = self.client.get(reverse("cafeteria:economic_export_csv"), {"period": "calendar", "year": timezone.localdate().year})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Donació", response.content.decode())
