from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.template.loader import get_template
from django.urls import reverse
from django.utils import timezone
from django.utils import translation

from .models import (
    AcademicYear,
    BookingStatus,
    CourseClosure,
    CourseGroup,
    DailyReportRecipient,
    Diet,
    Family,
    FamilyMembership,
    FamilyImportBatch,
    Invitation,
    MealBooking,
    MealType,
    MealPlan,
    MealSettings,
    PriceRule,
    ServiceDay,
    StatementStatus,
    Student,
    TeacherMealBooking,
    TeacherMealProfile,
)
from .services import expected_report_is_due, prepare_monthly_statement, prepare_teacher_monthly_statement
from .tasks import send_daily_report


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CafeteriaFlowTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.year = AcademicYear.objects.create(
            name="2026-2027",
            starts_on=self.today - timedelta(days=30),
            ends_on=self.today + timedelta(days=365),
            is_active=True,
        )
        self.group = CourseGroup.objects.create(academic_year=self.year, name="I4")
        self.family = Family.objects.create(name="Família Puig", billing_email="family@example.com")
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

    def test_joint_booking_copies_to_siblings_and_marks_packed_lunch(self):
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
        self.assertTrue(all(booking.meal_type == MealType.PACKED_LUNCH for booking in bookings))

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
        self.assertEqual(booking.meal_type, MealType.PACKED_LUNCH)

    def test_monthly_statement_uses_booked_price(self):
        MealBooking.objects.create(student=self.student, date=self.today, diet=self.diet)
        statement = prepare_monthly_statement(self.family, self.today.year, self.today.month)
        self.assertEqual(statement.total, Decimal("6.50"))
        self.assertEqual(statement.lines.count(), 1)

    def test_daily_report_is_emailed_to_configured_recipients(self):
        MealBooking.objects.create(student=self.student, date=self.today, diet=self.diet)
        meal_settings = MealSettings.objects.create(academic_year=self.year, daily_cutoff="09:00", daily_reports_enabled=True)
        DailyReportRecipient.objects.create(settings=meal_settings, email="cuina@example.com")
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

    @override_settings(EMAIL_HOST="")
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
        response = self.client.post(reverse("cafeteria:teacher_bulk_booking"), {
            "dates": [self.today.isoformat()], "action": "add",
        })
        self.assertEqual(response.status_code, 302)
        booking = TeacherMealBooking.objects.get(teacher=profile, date=self.today)
        self.assertEqual(booking.unit_price, Decimal("7.00"))
        statement = prepare_teacher_monthly_statement(profile, self.today.year, self.today.month)
        self.assertEqual(statement.total, Decimal("7.00"))

    def test_public_django_admin_route_is_disabled(self):
        self.assertEqual(self.client.get("/ca/admin/").status_code, 404)

    def test_csv_import_is_previewed_then_atomically_confirmed(self):
        admin = User.objects.create_superuser("admin@example.com", "admin@example.com", "correct-horse-battery-staple")
        self.client.force_login(admin)
        csv_content = (
            "family_name,billing_email,family_phone,family_address,student_first_name,student_last_name,"
            "birth_date,course_group,student_email,student_phone,contact_notes,default_diet,dietary_notes,scholarship,meal_plan\n"
            f"Família Nova,nova@example.com,600000000,,Arnau,Serra,2019-02-03,{self.group.name},,,,Ordinària,,No,Fix\n"
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
            "cafeteria:people",
            "cafeteria:school_calendar",
            "cafeteria:meal_configuration",
            "cafeteria:family_import",
        ):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(reverse(route)).status_code, 200)

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

    def test_member_dashboard_and_responsive_booking_view_render(self):
        self.client.force_login(self.user)
        with translation.override("ca"):
            self.assertEqual(self.client.get(reverse("cafeteria:dashboard")).status_code, 200)
            self.assertEqual(self.client.get(reverse("cafeteria:family_calendar", args=[self.family.id])).status_code, 200)

    def test_language_choice_is_saved_on_the_profile(self):
        self.client.force_login(self.user)
        with translation.override("ca"):
            response = self.client.post(reverse("cafeteria:set_language"), {"language": "es", "next": reverse("cafeteria:dashboard")})
        self.assertEqual(response.status_code, 302)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.language, "es")

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
            "cafeteria/audit_log.html", "cafeteria/family_import_preview.html",
        )
        for template in templates:
            with self.subTest(template=template):
                get_template(template)
