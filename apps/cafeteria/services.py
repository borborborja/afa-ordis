from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import (
    BookingStatus,
    MealBooking,
    MonthlyStatement,
    PriceRule,
    StatementLine,
    StatementStatus,
    TeacherMealBooking,
    TeacherMonthlyStatement,
    TeacherStatementLine,
)


def is_service_day(service_date: date, student=None) -> bool:
    from .models import AcademicHoliday, ServiceDay

    day = ServiceDay.objects.filter(date=service_date, is_service_day=True).first()
    if not day:
        return False
    if AcademicHoliday.objects.filter(
        academic_year=day.academic_year,
        starts_on__lte=service_date,
        ends_on__gte=service_date,
    ).exists():
        return False
    return True


def is_tutor_locked(service_date: date) -> bool:
    from .models import MealSettings

    now = timezone.localtime()
    if service_date < now.date():
        return True
    if service_date > now.date():
        return False
    settings = MealSettings.objects.filter(academic_year__starts_on__lte=service_date, academic_year__ends_on__gte=service_date).first()
    if not settings or not settings.daily_cutoff:
        return False
    return now.time() >= settings.daily_cutoff


def service_calendar(starts_on, ends_on):
    """Load availability and deadlines once for a whole calendar, shared by siblings."""
    from .models import AcademicHoliday, MealSettings, ServiceDay

    days = ServiceDay.objects.filter(date__range=(starts_on, ends_on), is_service_day=True)
    holidays = list(AcademicHoliday.objects.filter(starts_on__lte=ends_on, ends_on__gte=starts_on))
    available = {
        day.date for day in days
        if not any(holiday.academic_year_id == day.academic_year_id and holiday.starts_on <= day.date <= holiday.ends_on for holiday in holidays)
    }
    now = timezone.localtime()
    meal_settings = MealSettings.objects.filter(
        academic_year__starts_on__lte=now.date(), academic_year__ends_on__gte=now.date(),
    ).first()
    today_locked = bool(meal_settings and meal_settings.daily_cutoff and now.time() >= meal_settings.daily_cutoff)
    return available, now.date(), today_locked


def bookings_for_day(service_date: date):
    return MealBooking.objects.filter(date=service_date, status=BookingStatus.ACTIVE).select_related("student", "student__course_group", "diet")


def teacher_bookings_for_day(service_date: date):
    return TeacherMealBooking.objects.filter(date=service_date, status=BookingStatus.ACTIVE).select_related("teacher__user", "diet")


def build_daily_report_text(service_date: date) -> str:
    bookings = bookings_for_day(service_date)
    lines = [_("Llistat de menjador — %(date)s") % {"date": service_date.strftime("%d/%m/%Y")}, ""]
    by_diet = {}
    allergy_alerts = []
    for booking in bookings:
        course = booking.student.course_group.name if booking.student.course_group else _("Sense curs")
        diet = _("ATURA LA PREPARACIÓ") if booking.student.meal_safety_hold else booking.diet_name or _("Dieta ordinària")
        by_diet[diet] = by_diet.get(diet, 0) + 1
        lines.append(f"- {booking.student.full_name} · {course} · {diet}")
        if booking.student.has_operational_allergy_alert:
            status = _("ATURA LA PREPARACIÓ") if booking.student.meal_safety_hold else _("PENDENT DE VALIDAR") if booking.student.allergy_review_status == "pending" else _("VALIDADA")
            allergy_alerts.append(
                f"- {booking.student.full_name} · "
                f"{'' if booking.student.meal_safety_hold else booking.student.kitchen_instructions} [{status}]"
            )
    teacher_bookings = teacher_bookings_for_day(service_date)
    if teacher_bookings.exists():
        lines.extend(["", _("Personal docent")])
        for booking in teacher_bookings:
            diet = booking.diet_name or _("Dieta ordinària")
            by_diet[diet] = by_diet.get(diet, 0) + 1
            lines.append(f"- {booking.teacher.full_name} · {diet}")
    lines.extend(["", _("ATENCIÓ — AL·LÈRGIES")])
    if allergy_alerts:
        lines.extend(allergy_alerts)
    else:
        lines.append(_("Cap al·lèrgia declarada entre els àpats programats."))
    lines.append("")
    lines.append(_("Total: %(total)s") % {"total": bookings.count() + teacher_bookings.count()})
    lines.extend(f"{diet}: {total}" for diet, total in sorted(by_diet.items()))
    return "\n".join(lines)


@transaction.atomic
def prepare_monthly_statement(family, year: int, month: int) -> MonthlyStatement:
    statement, _created = MonthlyStatement.objects.get_or_create(family=family, year=year, month=month)
    if statement.status != StatementStatus.PREPARED:
        return statement

    statement.lines.all().delete()
    bookings = MealBooking.objects.filter(
        student__family=family,
        date__year=year,
        date__month=month,
        status=BookingStatus.ACTIVE,
    ).select_related("student")
    if bookings.filter(unit_price__isnull=True).exists():
        raise ValidationError(_("Hi ha reserves sense tarifa. Configura els preus abans de preparar o tancar el resum."))
    lines = []
    for booking in bookings:
        if booking.unit_price is None:
            continue
        lines.append(StatementLine(
            statement=statement,
            student=booking.student,
            service_date=booking.date,
            diet_name="",
            meal_plan=booking.student.meal_plan,
            scholarship=booking.student.is_scholarship,
            unit_price=booking.unit_price,
        ))
    StatementLine.objects.bulk_create(lines)
    statement.total = statement.lines.aggregate(total=Sum("unit_price"))["total"] or Decimal("0.00")
    statement.save(update_fields=["total"])
    return statement


@transaction.atomic
def prepare_teacher_monthly_statement(teacher, year: int, month: int) -> TeacherMonthlyStatement:
    statement, _created = TeacherMonthlyStatement.objects.get_or_create(teacher=teacher, year=year, month=month)
    if statement.status != StatementStatus.PREPARED:
        return statement
    statement.lines.all().delete()
    bookings = TeacherMealBooking.objects.filter(
        teacher=teacher, date__year=year, date__month=month, status=BookingStatus.ACTIVE,
    )
    if bookings.filter(unit_price__isnull=True).exists():
        raise ValidationError(_("Hi ha reserves sense tarifa. Configura els preus abans de preparar o tancar el resum."))
    TeacherStatementLine.objects.bulk_create([
        TeacherStatementLine(
            statement=statement, service_date=booking.date, diet_name="",
            meal_plan=teacher.meal_plan, unit_price=booking.unit_price,
        )
        for booking in bookings if booking.unit_price is not None
    ])
    statement.total = statement.lines.aggregate(total=Sum("unit_price"))["total"] or Decimal("0.00")
    statement.save(update_fields=["total"])
    return statement


def prepare_statements_for_month(year: int, month: int) -> int:
    from .models import Family, TeacherMealProfile

    count = 0
    for family in Family.objects.filter(active=True):
        prepare_monthly_statement(family, year, month)
        count += 1
    for teacher in TeacherMealProfile.objects.filter(active=True):
        prepare_teacher_monthly_statement(teacher, year, month)
        count += 1
    return count


def reprice_open_bookings(student=None, rule=None) -> int:
    """Actualitza reserves encara no tancades; l'històric mensual no es toca."""
    bookings = MealBooking.objects.filter(status=BookingStatus.ACTIVE).select_related("student")
    if student is not None:
        bookings = bookings.filter(student=student)
    if rule is not None:
        bookings = bookings.filter(
            student__is_scholarship=rule.scholarship,
            student__meal_plan=rule.meal_plan,
            date__gte=rule.effective_from,
        )
    closed_periods = set(
        MonthlyStatement.objects.filter(status__in=["closed", "sent"]).values_list("family_id", "year", "month")
    )
    changed = 0
    for booking in bookings:
        if (booking.student.family_id, booking.date.year, booking.date.month) in closed_periods:
            continue
        amount = PriceRule.amount_for(booking.student, booking.date)
        if booking.unit_price != amount:
            booking.unit_price = amount
            booking.save(update_fields=["unit_price", "updated_at"])
            changed += 1
    if student is not None:
        return changed
    teacher_bookings = TeacherMealBooking.objects.filter(status=BookingStatus.ACTIVE).select_related("teacher")
    if rule is not None:
        if rule.scholarship:
            return changed
        teacher_bookings = teacher_bookings.filter(teacher__meal_plan=rule.meal_plan, date__gte=rule.effective_from)
    closed_teacher_periods = set(
        TeacherMonthlyStatement.objects.filter(status__in=["closed", "sent"]).values_list("teacher_id", "year", "month")
    )
    for booking in teacher_bookings:
        if (booking.teacher_id, booking.date.year, booking.date.month) in closed_teacher_periods:
            continue
        amount = PriceRule.amount_for_category(False, booking.teacher.meal_plan, booking.date)
        if booking.unit_price != amount:
            booking.unit_price = amount
            booking.save(update_fields=["unit_price", "updated_at"])
            changed += 1
    return changed


def expected_report_is_due(settings, now=None) -> bool:
    now = now or timezone.localtime()
    return bool(
        settings.daily_reports_enabled
        and settings.daily_report_send_time
        and now.time() >= settings.daily_report_send_time
        and settings.daily_recipients.filter(active=True).exists()
    )
