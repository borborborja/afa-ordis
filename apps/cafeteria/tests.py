from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase, override_settings
from django.template.loader import get_template
from django.urls import reverse
from django.utils import timezone
from django.utils import translation

from .models import (
    AcademicHoliday,
    AcademicIntensivePeriod,
    AcademicNotice,
    AcademicYear,
    AfaFeeSettings,
    AfaMembership,
    AfaMembershipStatus,
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
from .services import expected_report_is_due, is_service_day, prepare_monthly_statement, prepare_teacher_monthly_statement
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
        self.assertNotContains(response, "Setmana del")

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
        update_url = reverse("cafeteria:family_booking_update", args=[self.family.id])
        self.client.post(update_url, {
            "student_id": self.student.id, "service_date": self.today.isoformat(), "operation": "reserve",
        })
        response = self.client.post(reverse("cafeteria:family_booking_apply", args=[self.family.id]), {
            "source_student_id": self.student.id,
            "service_date": self.today.isoformat(),
            "student_ids": [sibling.id],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 1)
        booking = MealBooking.objects.get(student=sibling, date=self.today)
        self.assertEqual(booking.diet, sibling_diet)

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
        self.assertEqual(booking.meal_type, MealType.PACKED_LUNCH)

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
            "billing_email": "nou-contacte@example.com",
            "phone": "600111222",
            "address": "Carrer Major, 1",
            "monthly_email_enabled": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.family.refresh_from_db()
        self.assertEqual(self.family.billing_email, "nou-contacte@example.com")

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
        self.assertEqual(booking.meal_type, MealType.PACKED_LUNCH)
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
        self.assertEqual(booking.meal_type, MealType.REGULAR)

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
            "cafeteria/audit_log.html", "cafeteria/family_import_preview.html", "cafeteria/family_home.html",
            "cafeteria/family_profile.html", "cafeteria/family_school_calendar.html",
        )
        for template in templates:
            with self.subTest(template=template):
                get_template(template)


class PortalBackupTests(TransactionTestCase):
    def test_administrator_can_download_a_sqlite_backup_from_the_portal(self):
        admin = User.objects.create_superuser("backup@example.com", "backup@example.com", "correct-horse-battery-staple")
        self.client.force_login(admin)
        response = self.client.post(reverse("cafeteria:portal_backup_download"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/vnd.sqlite3")
        self.assertIn("attachment; filename=", response["Content-Disposition"])
        self.assertGreater(len(response.content), 0)
