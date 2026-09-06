from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django.urls import reverse

from .models import (
    DailyReport,
    MealSettings,
    MonthlyStatement,
    MonthlyPreparation,
    StatementStatus,
    TeacherMonthlyStatement,
    ServiceDay,
    log_event,
)
from .services import expected_report_is_due, is_service_day, prepare_statements_for_month

logger = logging.getLogger(__name__)


def _statement_text(statement: MonthlyStatement) -> str:
    return _notice_text("cafeteria:monthly_statements")


def _notice_text(route):
    return _("Tens una actualització disponible al portal de l'AFA. Inicia sessió per consultar-la.") + "\n\n" + settings.APP_BASE_URL + reverse(route)


def notify_portal(recipients, route):
    sent = False
    for address in sorted(set(recipients)):
        delivered = send_mail(
            subject=_("Actualització al portal de l'AFA"), message=_notice_text(route),
            from_email=settings.DEFAULT_FROM_EMAIL, recipient_list=[address], fail_silently=False,
        )
        if not delivered:
            return False
        sent = True
    return sent


def send_daily_report(service_date_iso: str, actor_id: int | None = None) -> bool:
    from datetime import date
    from django.contrib.auth import get_user_model

    service_date = date.fromisoformat(service_date_iso)
    service_day = ServiceDay.objects.filter(date=service_date, is_service_day=True).first()
    if not service_day or not is_service_day(service_date):
        return False
    meal_settings = MealSettings.objects.filter(academic_year=service_day.academic_year).first()
    if not meal_settings:
        return False
    from .models import Role
    from .privacy import explicit_role
    recipients = []
    accounts = []
    for recipient in meal_settings.daily_recipients.filter(active=True).select_related("user"):
        account = recipient.user
        if account is None:
            account = get_user_model().objects.filter(email__iexact=recipient.email, is_active=True).first()
        if account and account.is_active and (account.is_superuser or explicit_role(account, Role.KITCHEN, Role.ADMIN, Role.MANAGER)):
            recipients.append(account.email)
            accounts.append(account)
    if not recipients:
        return False

    report, _created = DailyReport.objects.get_or_create(date=service_date)
    sent = all(notify_portal([account.email], "cafeteria:kitchen_report" if explicit_role(account, Role.KITCHEN) else "cafeteria:daily_reports") for account in accounts)
    if not sent:
        return False
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
    if not notify_portal(recipients, "cafeteria:monthly_statements"):
        return False
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
    if not notify_portal([statement.teacher.user.email], "cafeteria:monthly_statements"):
        return False
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
        if DailyReport.objects.filter(date=today, sent_at__isnull=False).exists():
            continue
        try:
            if send_daily_report(today.isoformat()):
                count += 1
        except Exception:
            logger.exception("No s'ha pogut enviar l'informe diari de %s", today)
    return count


@transaction.atomic
def prepare_due_monthly_statements() -> int:
    now = timezone.localtime()
    if now.month == 1:
        target_year, target_month = now.year - 1, 12
    else:
        target_year, target_month = now.year, now.month - 1
    if MonthlyPreparation.objects.filter(year=target_year, month=target_month).exists():
        return 0
    period_start = date(target_year, target_month, 1)
    period_end = date(target_year, target_month, monthrange(target_year, target_month)[1])
    for meal_settings in MealSettings.objects.filter(
        monthly_statements_enabled=True,
        academic_year__starts_on__lte=period_end,
        academic_year__ends_on__gte=period_start,
    ):
        if now.day > meal_settings.monthly_preparation_day or (
            now.day == meal_settings.monthly_preparation_day and now.time() >= meal_settings.monthly_preparation_hour
        ):
            count = prepare_statements_for_month(target_year, target_month)
            MonthlyPreparation.objects.create(year=target_year, month=target_month)
            return count
    return 0
