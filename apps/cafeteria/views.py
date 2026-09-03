from __future__ import annotations

import calendar
import csv
from datetime import date, datetime, timedelta
from io import StringIO

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.core.mail import send_mail
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from .forms import InvitationAcceptanceForm, InvitationForm, PriceRuleForm, TutorStudentForm
from .models import (
    AcademicYear,
    AuditEvent,
    BookingStatus,
    DailyReport,
    Diet,
    Family,
    FamilyMembership,
    Invitation,
    MealBooking,
    MealPlan,
    MonthlyStatement,
    PriceRule,
    Role,
    ServiceDay,
    StatementStatus,
    Student,
    ensure_role_groups,
    log_event,
    user_has_role,
)
from .services import is_service_day, is_tutor_locked, prepare_monthly_statement, reprice_open_bookings
from .tasks import send_daily_report, send_monthly_statement


def _is_staff(user):
    return user_has_role(user, Role.ADMIN, Role.MANAGER)


def _is_admin(user):
    return user_has_role(user, Role.ADMIN)


def staff_required(view):
    @login_required
    def wrapped(request, *args, **kwargs):
        if not _is_staff(request.user):
            return HttpResponseForbidden(_("No tens permís per accedir a aquesta pàgina."))
        return view(request, *args, **kwargs)
    return wrapped


def admin_required(view):
    @login_required
    def wrapped(request, *args, **kwargs):
        if not _is_admin(request.user):
            return HttpResponseForbidden(_("No tens permís per accedir a aquesta pàgina."))
        return view(request, *args, **kwargs)
    return wrapped


def _family_for_user_or_404(user, family_id):
    if user.is_superuser or _is_admin(user):
        return get_object_or_404(Family, pk=family_id)
    return get_object_or_404(Family, pk=family_id, memberships__user=user)


@require_GET
def healthcheck(request):
    return HttpResponse("ok", content_type="text/plain")


@login_required
def dashboard(request):
    today = timezone.localdate()
    if _is_staff(request.user):
        active_bookings = MealBooking.objects.filter(date=today, status=BookingStatus.ACTIVE).select_related("student", "diet")
        diets = {}
        for booking in active_bookings:
            name = booking.diet_name or _("Ordinària")
            diets[name] = diets.get(name, 0) + 1
        context = {
            "is_staff": True,
            "today": today,
            "today_bookings": active_bookings[:12],
            "today_total": active_bookings.count(),
            "diet_totals": sorted(diets.items()),
            "pending_statements": MonthlyStatement.objects.filter(status=StatementStatus.PREPARED).count(),
            "outdated_reports": DailyReport.objects.filter(is_outdated=True).count(),
        }
        return render(request, "cafeteria/dashboard_staff.html", context)

    families = Family.objects.filter(memberships__user=request.user, active=True).prefetch_related("students__default_diet")
    upcoming = MealBooking.objects.filter(
        student__family__in=families,
        date__gte=today,
        status=BookingStatus.ACTIVE,
    ).select_related("student", "diet").order_by("date")[:8]
    return render(request, "cafeteria/dashboard_tutor.html", {"families": families, "upcoming": upcoming, "today": today})


@login_required
def family_calendar(request, family_id):
    family = _family_for_user_or_404(request.user, family_id)
    try:
        month_start = datetime.strptime(request.GET.get("month", ""), "%Y-%m").date().replace(day=1)
    except ValueError:
        month_start = timezone.localdate().replace(day=1)
    month_end = month_start.replace(day=calendar.monthrange(month_start.year, month_start.month)[1])

    students = list(family.students.filter(active=True).select_related("default_diet", "course_group"))
    existing = MealBooking.objects.filter(student__in=students, date__range=(month_start, month_end)).select_related("diet")
    booking_map = {(booking.student_id, booking.date): booking for booking in existing}
    global_days = {day.date: day for day in ServiceDay.objects.filter(date__range=(month_start, month_end))}
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(month_start.year, month_start.month)
    student_calendars = []
    for student in students:
        grid = []
        for week in weeks:
            week_cells = []
            for day in week:
                booking = booking_map.get((student.id, day))
                available = day.month == month_start.month and is_service_day(day, student)
                week_cells.append({
                    "date": day,
                    "available": available,
                    "current_month": day.month == month_start.month,
                    "booking": booking,
                    "locked": is_tutor_locked(day) and not _is_staff(request.user),
                })
            grid.append(week_cells)
        student_calendars.append({"student": student, "weeks": grid})

    previous_month = (month_start.replace(day=1) - timedelta(days=1)).replace(day=1)
    next_month = (month_end + timedelta(days=1)).replace(day=1)
    return render(request, "cafeteria/family_calendar.html", {
        "family": family,
        "student_calendars": student_calendars,
        "diets": Diet.objects.filter(active=True),
        "month_start": month_start,
        "previous_month": previous_month,
        "next_month": next_month,
        "is_staff": _is_staff(request.user),
    })


@require_POST
@login_required
def bulk_booking(request, family_id):
    family = _family_for_user_or_404(request.user, family_id)
    student = get_object_or_404(Student, pk=request.POST.get("student_id"), family=family, active=True)
    selected_dates = []
    for raw_date in request.POST.getlist("dates"):
        try:
            selected_dates.append(date.fromisoformat(raw_date))
        except ValueError:
            continue
    action = request.POST.get("action")
    diet = Diet.objects.filter(pk=request.POST.get("diet_id"), active=True).first()
    reason = request.POST.get("override_reason", "").strip()
    success, skipped = 0, 0
    for service_date in selected_dates:
        locked = is_tutor_locked(service_date)
        if locked and not _is_staff(request.user):
            skipped += 1
            continue
        if locked and _is_staff(request.user) and not reason:
            messages.error(request, _("Cal indicar el motiu de qualsevol canvi després de l'hora límit."))
            return redirect(f"{reverse('cafeteria:family_calendar', args=[family.id])}?month={service_date:%Y-%m}")
        if not is_service_day(service_date, student):
            skipped += 1
            continue

        booking = MealBooking.objects.filter(student=student, date=service_date).first()
        if action == "cancel":
            if booking and booking.status == BookingStatus.ACTIVE:
                booking.status = BookingStatus.CANCELLED
                booking.updated_by = request.user
                booking.override_reason = reason
                booking.save(update_fields=["status", "updated_by", "override_reason", "updated_at"])
                log_event(request.user, "booking.cancelled", booking, {"after_cutoff": locked, "reason": reason})
                success += 1
            continue

        selected_diet = diet or student.default_diet
        if booking:
            booking.status = BookingStatus.ACTIVE
            booking.diet = selected_diet
            booking.diet_name = selected_diet.name if selected_diet else _("Ordinària")
            booking.updated_by = request.user
            booking.override_reason = reason
            booking.unit_price = PriceRule.amount_for(student, service_date)
            booking.save()
        else:
            booking = MealBooking.objects.create(
                student=student,
                date=service_date,
                diet=selected_diet,
                diet_name=selected_diet.name if selected_diet else _("Ordinària"),
                created_by=request.user,
                updated_by=request.user,
                override_reason=reason,
            )
        log_event(request.user, "booking.created_or_updated", booking, {"after_cutoff": locked, "reason": reason})
        success += 1
        DailyReport.objects.filter(date=service_date, sent_at__isnull=False).update(is_outdated=True)

    if success:
        messages.success(request, _("S'han actualitzat %(count)s dies de menjador.") % {"count": success})
    if skipped:
        messages.warning(request, _("S'han ignorat %(count)s dies no disponibles o bloquejats.") % {"count": skipped})
    month = selected_dates[0].strftime("%Y-%m") if selected_dates else timezone.localdate().strftime("%Y-%m")
    return redirect(f"{reverse('cafeteria:family_calendar', args=[family.id])}?month={month}")


@login_required
def student_edit(request, student_id):
    if request.user.is_superuser or _is_admin(request.user):
        student = get_object_or_404(Student, pk=student_id)
    else:
        student = get_object_or_404(Student, pk=student_id, family__memberships__user=request.user)
    form = TutorStudentForm(request.POST or None, instance=student)
    if request.method == "POST" and form.is_valid():
        updated = form.save()
        reprice_open_bookings(student=updated)
        log_event(request.user, "student.updated_by_tutor", updated)
        messages.success(request, _("S'ha actualitzat la fitxa de %(name)s.") % {"name": updated.full_name})
        return redirect("cafeteria:family_calendar", family_id=updated.family_id)
    return render(request, "cafeteria/student_form.html", {"form": form, "student": student})


@admin_required
def invitation_create(request):
    form = InvitationForm(request.POST or None)
    invitation_url = None
    if request.method == "POST" and form.is_valid():
        invitation = form.save(commit=False)
        invitation.created_by = request.user
        invitation.save()
        invitation_url = f"{settings.APP_BASE_URL.rstrip('/')}{reverse('cafeteria:invitation_accept', args=[invitation.token])}"
        try:
            send_mail(
                subject=_("Invitació al portal AFA Ordis"),
                message=_("Has rebut una invitació. Crea el teu compte aquí:\n%(url)s") % {"url": invitation_url},
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[invitation.email],
                fail_silently=False,
            )
            invitation.sent_at = timezone.now()
            invitation.save(update_fields=["sent_at"])
            messages.success(request, _("S'ha enviat la invitació. També en pots copiar l'enllaç.") )
        except Exception:
            messages.warning(request, _("La invitació s'ha creat, però no s'ha pogut enviar el correu. Copia l'enllaç manualment."))
        log_event(request.user, "invitation.created", invitation, {"email": invitation.email, "role": invitation.role})
        form = InvitationForm()
    return render(request, "cafeteria/invitation_form.html", {"form": form, "invitation_url": invitation_url})


def _finish_invitation(invitation, user):
    ensure_role_groups()
    user.groups.add(Group.objects.get(name=invitation.role))
    if invitation.role == Role.ADMIN:
        user.is_staff = True
        user.save(update_fields=["is_staff"])
    if invitation.role == Role.TUTOR:
        FamilyMembership.objects.get_or_create(family=invitation.family, user=user)
    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=["accepted_at"])
    log_event(user, "invitation.accepted", invitation, {"role": invitation.role})


def invitation_accept(request, token):
    invitation = get_object_or_404(Invitation, token=token)
    if not invitation.is_valid:
        raise Http404(_("Aquesta invitació ha caducat o ja s'ha utilitzat."))
    existing = User.objects.filter(email__iexact=invitation.email).first()
    if existing:
        if not request.user.is_authenticated:
            messages.info(request, _("Inicia sessió amb el compte convidat per acceptar la invitació."))
            return redirect(f"{reverse('cafeteria:login')}?next={request.path}")
        if request.user.pk != existing.pk:
            return HttpResponseForbidden(_("Aquesta invitació correspon a un altre compte."))
        _finish_invitation(invitation, existing)
        messages.success(request, _("La invitació s'ha acceptat correctament."))
        return redirect("cafeteria:dashboard")

    user = User(username=invitation.email.lower(), email=invitation.email.lower())
    form = InvitationAcceptanceForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        _finish_invitation(invitation, user)
        login(request, user)
        messages.success(request, _("El teu compte ja està actiu."))
        return redirect("cafeteria:dashboard")
    return render(request, "cafeteria/invitation_accept.html", {"form": form, "invitation": invitation})


@staff_required
def price_rules(request):
    form = PriceRuleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        rule = form.save(commit=False)
        rule.created_by = request.user
        rule.save()
        reprice_open_bookings(rule=rule)
        log_event(request.user, "price_rule.created", rule, {"amount": str(rule.amount)})
        messages.success(request, _("S'ha guardat la tarifa amb data d'efecte."))
        return redirect("cafeteria:price_rules")
    rules = PriceRule.objects.all()
    return render(request, "cafeteria/price_rules.html", {"form": form, "rules": rules})


@staff_required
def daily_reports(request):
    reports = DailyReport.objects.all()[:50]
    return render(request, "cafeteria/daily_reports.html", {"reports": reports, "today": timezone.localdate()})


@staff_required
@require_POST
def daily_report_send(request, service_date):
    try:
        report_date = date.fromisoformat(service_date)
    except ValueError:
        raise Http404(_("Data no vàlida."))
    try:
        sent = send_daily_report(report_date.isoformat(), request.user.id)
    except Exception:
        messages.error(request, _("No s'ha pogut enviar l'informe. Revisa la configuració SMTP."))
    else:
        messages.success(request, _("S'ha enviat l'informe.") if sent else _("No hi ha configuració de destinataris per a aquest dia."))
    return redirect("cafeteria:daily_reports")


def _statement_is_visible_to_user(statement, user):
    return user.is_superuser or _is_staff(user) or statement.family.memberships.filter(user=user).exists()


@login_required
def monthly_statements(request):
    statements = MonthlyStatement.objects.select_related("family")
    if not _is_staff(request.user):
        statements = statements.filter(family__memberships__user=request.user)
    return render(request, "cafeteria/monthly_statements.html", {"statements": statements[:100], "is_staff": _is_staff(request.user)})


@staff_required
@require_POST
def statement_prepare(request):
    try:
        year = int(request.POST["year"])
        month = int(request.POST["month"])
        if not 1 <= month <= 12:
            raise ValueError
    except (KeyError, ValueError):
        messages.error(request, _("Mes no vàlid."))
        return redirect("cafeteria:monthly_statements")
    count = 0
    for family in Family.objects.filter(active=True):
        prepare_monthly_statement(family, year, month)
        count += 1
    messages.success(request, _("S'han preparat %(count)s resums familiars.") % {"count": count})
    return redirect("cafeteria:monthly_statements")


@login_required
def statement_detail(request, statement_id):
    statement = get_object_or_404(MonthlyStatement.objects.select_related("family"), pk=statement_id)
    if not _statement_is_visible_to_user(statement, request.user):
        return HttpResponseForbidden(_("No tens permís per veure aquest resum."))
    return render(request, "cafeteria/statement_detail.html", {"statement": statement, "is_staff": _is_staff(request.user)})


@staff_required
@require_POST
def statement_close(request, statement_id):
    statement = get_object_or_404(MonthlyStatement, pk=statement_id)
    if statement.status == StatementStatus.PREPARED:
        statement.status = StatementStatus.CLOSED
        statement.closed_at = timezone.now()
        statement.closed_by = request.user
        statement.save(update_fields=["status", "closed_at", "closed_by"])
        log_event(request.user, "monthly_statement.closed", statement)
    messages.success(request, _("El resum s'ha tancat."))
    return redirect("cafeteria:statement_detail", statement_id=statement.id)


@staff_required
@require_POST
def statement_send(request, statement_id):
    statement = get_object_or_404(MonthlyStatement, pk=statement_id)
    if statement.status == StatementStatus.PREPARED:
        messages.error(request, _("Cal tancar el resum abans d'enviar-lo."))
    else:
        try:
            sent = send_monthly_statement(statement.id, request.user.id)
        except Exception:
            messages.error(request, _("No s'ha pogut enviar el resum. Revisa la configuració SMTP."))
        else:
            messages.success(request, _("S'ha enviat el resum.") if sent else _("La família no té cap correu configurat."))
    return redirect("cafeteria:statement_detail", statement_id=statement.id)


@login_required
def statement_csv(request, statement_id):
    statement = get_object_or_404(MonthlyStatement.objects.select_related("family"), pk=statement_id)
    if not _statement_is_visible_to_user(statement, request.user):
        return HttpResponseForbidden(_("No tens permís per descarregar aquest resum."))
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="menjador-{statement.year}-{statement.month:02d}-{statement.family_id}.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(["Data", "Alumne", "Dieta", "Modalitat", "Becat", "Import"])
    for line in statement.lines.select_related("student"):
        writer.writerow([line.service_date.isoformat(), line.student.full_name, line.diet_name, line.get_meal_plan_display(), "Sí" if line.scholarship else "No", line.unit_price])
    writer.writerow([])
    writer.writerow(["Total", "", "", "", "", statement.total])
    return response


@admin_required
def audit_log(request):
    return render(request, "cafeteria/audit_log.html", {"events": AuditEvent.objects.select_related("actor")[:200]})
