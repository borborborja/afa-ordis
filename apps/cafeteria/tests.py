from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    AcademicYear,
    BookingStatus,
    CourseClosure,
    CourseGroup,
    DailyReportRecipient,
    Diet,
    Family,
    FamilyMembership,
    MealBooking,
    MealPlan,
    MealSettings,
    PriceRule,
    ServiceDay,
    StatementStatus,
    Student,
)
from .services import prepare_monthly_statement
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

    def test_tutor_cannot_change_scholarship_status(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("cafeteria:student_edit", args=[self.student.id]), {
            "first_name": "Núria",
            "last_name": "Puig",
            "course_group": self.group.id,
            "meal_plan": MealPlan.SPORADIC,
            "is_scholarship": "on",
            "active": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.student.refresh_from_db()
        self.assertFalse(self.student.is_scholarship)
        self.assertEqual(self.student.meal_plan, MealPlan.SPORADIC)

    @patch("apps.cafeteria.tasks.send_course_closure_notification")
    def test_course_closure_cancels_existing_bookings(self, delayed_email):
        booking = MealBooking.objects.create(student=self.student, date=self.today, diet=self.diet)
        CourseClosure.objects.create(course_group=self.group, date=self.today, title="Excursió")
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CANCELLED)

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

    def test_password_reset_uses_the_namespaced_portal_url(self):
        response = self.client.post(reverse("cafeteria:password_reset"), {"email": self.user.email})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/ca/comptes/contrasenya/", mail.outbox[0].body)
