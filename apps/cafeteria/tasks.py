from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import (
    BookingStatus,
    DailyReport,
    MealSettings,
    MonthlyStatement,
    StatementStatus,
    TeacherMonthlyStatement,
    log_event,
)
from .services import build_daily_report_text, expected_report_is_due, is_service_day, prepare_statements_for_month

logger = logging.getLogger(__name__)


def _statement_text(statement: MonthlyStatement) -> str:
    lines = [
        f"Resum de menjador — {statement.month:02d}/{statement.year}",
        f"Família: {statement.family.name}",
        "",
    ]
    for line in statement.lines.select_related("student"):
        meal = "Carmanyola" if line.meal_type == "packed_lunch" else (line.diet_name or "Dieta ordinària")
        lines.append(f"- {line.service_date:%d/%m/%Y}: {line.student.full_name} · {meal} · {line.unit_price:.2f} €")
    lines += ["", f"Total: {statement.total:.2f} €"]
    return "\n".join(lines)


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


def send_teacher_monthly_statement(statement_id: int, actor_id: int | None = None) -> bool:
    from django.contrib.auth import get_user_model

    statement = TeacherMonthlyStatement.objects.select_related("teacher__user").filter(
        pk=statement_id, status__in=[StatementStatus.CLOSED, StatementStatus.SENT]
    ).first()
    if not statement or not statement.teacher.user.email:
        return False
    lines = [
        f"Resum de menjador — {statement.month:02d}/{statement.year}",
        f"Persona: {statement.teacher.full_name}", "",
    ]
    for line in statement.lines.all():
        meal = "Carmanyola" if line.meal_type == "packed_lunch" else (line.diet_name or "Dieta ordinària")
        lines.append(f"- {line.service_date:%d/%m/%Y}: {meal} · {line.unit_price:.2f} €")
    lines += ["", f"Total: {statement.total:.2f} €"]
    send_mail(
        subject=f"Resum de menjador · {statement.month:02d}/{statement.year}",
        message="\n".join(lines), from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[statement.teacher.user.email], fail_silently=False,
    )
    actor = get_user_model().objects.filter(pk=actor_id).first() if actor_id else None
    statement.status = StatementStatus.SENT
    statement.sent_at = timezone.now()
    statement.save(update_fields=["status", "sent_at"])
    log_event(actor, "teacher_monthly_statement.sent", statement, {"recipients": [statement.teacher.user.email]})
    return True


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
        try:
            if send_daily_report(today.isoformat()):
                count += 1
        except Exception:
            logger.exception("No s'ha pogut enviar l'informe diari de %s", today)
    return count


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
