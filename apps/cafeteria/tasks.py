from __future__ import annotations

from calendar import monthrange
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import (
    BookingStatus,
    DailyReport,
    MealSettings,
    MonthlyStatement,
    StatementStatus,
    log_event,
)
from .services import build_daily_report_text, expected_report_is_due, is_service_day, prepare_statements_for_month


def _statement_text(statement: MonthlyStatement) -> str:
    lines = [
        f"Resum de menjador — {statement.month:02d}/{statement.year}",
        f"Família: {statement.family.name}",
        "",
    ]
    for line in statement.lines.select_related("student"):
        lines.append(f"- {line.service_date:%d/%m/%Y}: {line.student.full_name} · {line.diet_name or 'Dieta ordinària'} · {line.unit_price:.2f} €")
    lines += ["", f"Total: {statement.total:.2f} €"]
    return "\n".join(lines)


@shared_task
def send_daily_report(service_date_iso: str, actor_id: int | None = None) -> bool:
    from datetime import date
    from django.contrib.auth import get_user_model

    service_date = date.fromisoformat(service_date_iso)
    service_day = __import__("apps.cafeteria.models", fromlist=["ServiceDay"]).ServiceDay.objects.filter(date=service_date, is_service_day=True).first()
    if not service_day:
        return False
    meal_settings = MealSettings.objects.filter(academic_year=service_day.academic_year).first()
    if not meal_settings:
        return False
    recipients = list(meal_settings.daily_recipients.filter(active=True).values_list("email", flat=True))
    if not recipients:
        return False

    report, _ = DailyReport.objects.get_or_create(date=service_date)
    send_mail(
        subject=f"Llistat de menjador · {service_date:%d/%m/%Y}",
        message=build_daily_report_text(service_date),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipients,
        fail_silently=False,
    )
    actor = get_user_model().objects.filter(pk=actor_id).first() if actor_id else None
    report.sent_at = timezone.now()
    report.sent_by = actor
    report.recipients = recipients
    report.is_outdated = False
    report.save(update_fields=["sent_at", "sent_by", "recipients", "is_outdated"])
    log_event(actor, "daily_report.sent", report, {"recipients": recipients})
    return True


@shared_task
def send_course_closure_notification(closure_id: int, family_ids: list[int] | None = None) -> int:
    from .models import CourseClosure, Family

    closure = CourseClosure.objects.select_related("course_group").filter(pk=closure_id).first()
    if not closure:
        return 0
    families = Family.objects.filter(pk__in=family_ids or [], monthly_email_enabled=True)
    sent = 0
    for family in families:
        recipients = family.recipient_emails()
        if not recipients:
            continue
        send_mail(
            subject=f"Canvi de menjador · {closure.date:%d/%m/%Y}",
            message=(
                f"S'ha anul·lat el servei de menjador del dia {closure.date:%d/%m/%Y} "
                f"per a {closure.course_group.name} ({closure.title}). "
                "Les reserves afectades no es facturaran."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=False,
        )
        sent += 1
    return sent


@shared_task
def send_monthly_statement(statement_id: int, actor_id: int | None = None) -> bool:
    from django.contrib.auth import get_user_model

    statement = MonthlyStatement.objects.select_related("family").filter(pk=statement_id, status__in=[StatementStatus.CLOSED, StatementStatus.SENT]).first()
    if not statement or not statement.family.monthly_email_enabled:
        return False
    recipients = statement.family.recipient_emails()
    if not recipients:
        return False
    send_mail(
        subject=f"Resum de menjador · {statement.month:02d}/{statement.year}",
        message=_statement_text(statement),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipients,
        fail_silently=False,
    )
    actor = get_user_model().objects.filter(pk=actor_id).first() if actor_id else None
    statement.status = StatementStatus.SENT
    statement.sent_at = timezone.now()
    statement.save(update_fields=["status", "sent_at"])
    log_event(actor, "monthly_statement.sent", statement, {"recipients": recipients})
    return True


@shared_task
def send_due_daily_reports() -> int:
    today = timezone.localdate()
    count = 0
    for meal_settings in MealSettings.objects.filter(
        academic_year__starts_on__lte=today,
        academic_year__ends_on__gte=today,
    ):
        if not expected_report_is_due(meal_settings):
            continue
        if not is_service_day(today):
            continue
        if DailyReport.objects.filter(date=today).exists():
            continue
        send_daily_report.delay(today.isoformat())
        count += 1
    return count


@shared_task
def prepare_due_monthly_statements() -> int:
    now = timezone.localtime()
    if now.month == 1:
        target_year, target_month = now.year - 1, 12
    else:
        target_year, target_month = now.year, now.month - 1
    count = 0
    for meal_settings in MealSettings.objects.filter(monthly_statements_enabled=True):
        if now.day == meal_settings.monthly_preparation_day and now.time() >= meal_settings.monthly_preparation_hour:
            count += prepare_statements_for_month(target_year, target_month)
    return count
