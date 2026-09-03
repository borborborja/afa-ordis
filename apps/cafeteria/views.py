from __future__ import annotations

import calendar
import csv
import hashlib
from datetime import date, datetime, timedelta
from io import StringIO

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils import translation
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from .forms import (
    AcademicYearForm,
    CourseClosureForm,
    CourseGroupForm,
    CSVImportForm,
    DailyReportRecipientForm,
    DietForm,
    FamilyForm,
    InvitationAcceptanceForm,
    InvitationForm,
    MealSettingsForm,
    PriceRuleForm,
    StaffStudentForm,
    TutorStudentForm,
)
from .models import (
    AcademicYear,
    AuditEvent,
    BookingStatus,
    CourseClosure,
    CourseGroup,
    DailyReport,
    DailyReportRecipient,
    Diet,
    Family,
    FamilyImportBatch,
    FamilyMembership,
    Invitation,
    MealBooking,
    MealSettings,
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

    try:
        week_start = date.fromisoformat(request.GET.get("week", ""))
        week_start -= timedelta(days=week_start.weekday())
    except ValueError:
        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=4)
    week_bookings = MealBooking.objects.filter(student__in=students, date__range=(week_start, week_end)).select_related("diet")
    weekly_booking_map = {(booking.student_id, booking.date): booking for booking in week_bookings}
    weekly_calendars = []
    for student in students:
        days = []
        for offset in range(5):
            service_date = week_start + timedelta(days=offset)
            booking = weekly_booking_map.get((student.id, service_date))
            days.append({
                "date": service_date, "available": is_service_day(service_date, student), "booking": booking,
                "locked": is_tutor_locked(service_date) and not _is_staff(request.user),
            })
        weekly_calendars.append({"student": student, "days": days})

    previous_month = (month_start.replace(day=1) - timedelta(days=1)).replace(day=1)
    next_month = (month_end + timedelta(days=1)).replace(day=1)
    return render(request, "cafeteria/family_calendar.html", {
        "family": family,
        "student_calendars": student_calendars,
        "diets": Diet.objects.filter(active=True),
        "month_start": month_start,
        "previous_month": previous_month,
        "next_month": next_month,
        "week_start": week_start,
        "previous_week": week_start - timedelta(days=7),
        "next_week": week_start + timedelta(days=7),
        "weekly_calendars": weekly_calendars,
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


@require_POST
@login_required
def set_language(request):
    language = request.POST.get("language")
    supported = {code for code, _label in settings.LANGUAGES}
    if language in supported:
        request.session["django_language"] = language
        translation.activate(language)
        profile, _created = request.user.profile, False
        profile.language = language
        profile.save(update_fields=["language"])
    return redirect(request.POST.get("next") or reverse("cafeteria:dashboard"))


def _active_year_or_none():
    return AcademicYear.objects.filter(is_active=True).first() or AcademicYear.objects.first()


@admin_required
def management_dashboard(request):
    active_year = _active_year_or_none()
    today = timezone.localdate()
    return render(request, "cafeteria/management_dashboard.html", {
        "active_year": active_year,
        "family_count": Family.objects.filter(active=True).count(),
        "student_count": Student.objects.filter(active=True).count(),
        "pending_invitations": Invitation.objects.filter(accepted_at__isnull=True, expires_at__gt=timezone.now()).count(),
        "service_days": ServiceDay.objects.filter(academic_year=active_year, is_service_day=True).count() if active_year else 0,
        "today_total": MealBooking.objects.filter(date=today, status=BookingStatus.ACTIVE).count(),
        "recent_imports": FamilyImportBatch.objects.select_related("academic_year", "uploaded_by")[:5],
    })


@admin_required
def people(request):
    query = request.GET.get("q", "").strip()
    families = Family.objects.prefetch_related("students", "memberships__user")
    students = Student.objects.select_related("family", "course_group", "default_diet")
    if query:
        families = families.filter(name__icontains=query)
        students = students.filter(
            first_name__icontains=query
        ) | students.filter(last_name__icontains=query) | students.filter(family__name__icontains=query)
    return render(request, "cafeteria/people.html", {
        "families": families[:100],
        "students": students.order_by("family__name", "first_name")[:150],
        "query": query,
    })


@admin_required
def family_form(request, family_id=None):
    family = get_object_or_404(Family, pk=family_id) if family_id else None
    form = FamilyForm(request.POST or None, instance=family)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        log_event(request.user, "family.created" if family is None else "family.updated", saved)
        messages.success(request, _("S'ha desat la família."))
        return redirect("cafeteria:people")
    return render(request, "cafeteria/entity_form.html", {
        "form": form, "title": _("Nova família") if family is None else _("Edita la família"),
        "back_url": reverse("cafeteria:people"),
        "help_text": _("Les persones tutores s'afegeixen amb una invitació un cop creada la família."),
    })


@admin_required
def management_student_form(request, student_id=None):
    student = get_object_or_404(Student, pk=student_id) if student_id else None
    form = StaffStudentForm(request.POST or None, instance=student)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        reprice_open_bookings(student=saved)
        log_event(request.user, "student.created" if student is None else "student.updated", saved)
        messages.success(request, _("S'ha desat la fitxa de l'alumne."))
        return redirect("cafeteria:people")
    return render(request, "cafeteria/entity_form.html", {
        "form": form, "title": _("Nou alumne") if student is None else _("Edita la fitxa de l'alumne"),
        "back_url": reverse("cafeteria:people"),
    })


def _weekday_service_days(academic_year):
    current = academic_year.starts_on
    days = []
    while current <= academic_year.ends_on:
        if current.weekday() < 5:
            days.append(ServiceDay(academic_year=academic_year, date=current, is_service_day=True))
        current += timedelta(days=1)
    ServiceDay.objects.bulk_create(days, ignore_conflicts=True)
    return len(days)


@admin_required
def school_calendar(request):
    selected_id = request.GET.get("year")
    year = AcademicYear.objects.filter(pk=selected_id).first() if selected_id else _active_year_or_none()
    month_value = request.GET.get("month", "")
    try:
        month_start = datetime.strptime(month_value, "%Y-%m").date().replace(day=1)
    except ValueError:
        month_start = (year.starts_on if year else timezone.localdate()).replace(day=1)
    month_end = month_start.replace(day=calendar.monthrange(month_start.year, month_start.month)[1])
    service_days = {item.date: item for item in ServiceDay.objects.filter(academic_year=year, date__range=(month_start, month_end))}
    closures = CourseClosure.objects.filter(course_group__academic_year=year, date__range=(month_start, month_end)).select_related("course_group")
    previous_month = (month_start - timedelta(days=1)).replace(day=1)
    next_month = (month_end + timedelta(days=1)).replace(day=1)
    return render(request, "cafeteria/school_calendar.html", {
        "year": year, "years": AcademicYear.objects.all(), "month_start": month_start,
        "weeks": calendar.Calendar(firstweekday=0).monthdatescalendar(month_start.year, month_start.month),
        "service_days": service_days, "closures": closures, "previous_month": previous_month, "next_month": next_month,
        "closure_form": CourseClosureForm(initial={"date": month_start}),
    })


@admin_required
@require_POST
def academic_year_save(request, year_id=None):
    academic_year = get_object_or_404(AcademicYear, pk=year_id) if year_id else None
    form = AcademicYearForm(request.POST, instance=academic_year)
    if form.is_valid():
        saved = form.save()
        MealSettings.objects.get_or_create(academic_year=saved)
        log_event(request.user, "academic_year.created" if academic_year is None else "academic_year.updated", saved)
        messages.success(request, _("S'ha desat el curs acadèmic."))
    else:
        messages.error(request, _("No s'ha pogut desar el curs. Revisa les dates."))
    return redirect("cafeteria:school_calendar")


@admin_required
@require_POST
def generate_service_days(request, year_id):
    year = get_object_or_404(AcademicYear, pk=year_id)
    created = _weekday_service_days(year)
    log_event(request.user, "service_days.generated", year, {"weekday_rows": created})
    messages.success(request, _("S'han preparat els dies lectius de dilluns a divendres."))
    return redirect(f"{reverse('cafeteria:school_calendar')}?year={year.id}")


@admin_required
@require_POST
def service_day_toggle(request, service_date):
    day = get_object_or_404(ServiceDay, pk=request.POST.get("service_day"), date=service_date)
    day.is_service_day = request.POST.get("is_service_day") == "1"
    day.note = request.POST.get("note", "").strip()
    day.save(update_fields=["is_service_day", "note"])
    log_event(request.user, "service_day.updated", day, {"open": day.is_service_day})
    return redirect(request.POST.get("next") or "cafeteria:school_calendar")


@admin_required
@require_POST
def course_group_save(request, group_id=None):
    group = get_object_or_404(CourseGroup, pk=group_id) if group_id else None
    form = CourseGroupForm(request.POST, instance=group)
    if form.is_valid():
        saved = form.save()
        log_event(request.user, "course_group.created" if group is None else "course_group.updated", saved)
        messages.success(request, _("S'ha desat el curs o grup."))
    else:
        messages.error(request, _("No s'ha pogut desar el grup."))
    return redirect("cafeteria:school_calendar")


@admin_required
@require_POST
def course_closure_save(request):
    form = CourseClosureForm(request.POST)
    if form.is_valid():
        saved = form.save()
        log_event(request.user, "course_closure.created", saved)
        messages.success(request, _("S'ha registrat l'excursió i s'han anul·lat les reserves afectades."))
    else:
        messages.error(request, _("No s'ha pogut desar l'excursió."))
    return redirect(request.POST.get("next") or "cafeteria:school_calendar")


@admin_required
def meal_configuration(request):
    active_year = _active_year_or_none()
    settings_object = MealSettings.objects.filter(academic_year=active_year).first() if active_year else None
    if active_year and settings_object is None:
        settings_object = MealSettings.objects.create(academic_year=active_year)
    settings_form = MealSettingsForm(request.POST or None, instance=settings_object, prefix="settings") if settings_object else None
    diet_form = DietForm(request.POST or None, prefix="diet")
    recipient_form = DailyReportRecipientForm(request.POST or None, prefix="recipient") if settings_object else None
    if request.method == "POST":
        intent = request.POST.get("intent")
        if intent == "settings" and settings_form and settings_form.is_valid():
            saved = settings_form.save()
            log_event(request.user, "meal_settings.updated", saved)
            messages.success(request, _("S'ha actualitzat la configuració del menjador."))
            return redirect("cafeteria:meal_configuration")
        if intent == "diet" and diet_form.is_valid():
            saved = diet_form.save()
            log_event(request.user, "diet.created", saved)
            messages.success(request, _("S'ha afegit la dieta."))
            return redirect("cafeteria:meal_configuration")
        if intent == "recipient" and recipient_form and recipient_form.is_valid():
            saved = recipient_form.save(commit=False)
            saved.settings = settings_object
            saved.save()
            log_event(request.user, "daily_recipient.created", saved)
            messages.success(request, _("S'ha afegit el destinatari."))
            return redirect("cafeteria:meal_configuration")
        messages.error(request, _("Revisa les dades del formulari."))
    return render(request, "cafeteria/meal_configuration.html", {
        "active_year": active_year, "settings_form": settings_form, "diet_form": diet_form,
        "recipient_form": recipient_form, "diets": Diet.objects.all(),
        "recipients": settings_object.daily_recipients.all() if settings_object else [],
    })


CSV_COLUMNS = (
    "family_name,billing_email,family_phone,family_address,student_first_name,student_last_name,"
    "birth_date,course_group,student_email,student_phone,contact_notes,default_diet,dietary_notes,"
    "scholarship,meal_plan"
).split(",")


def _csv_boolean(raw):
    if raw.strip().lower() in {"1", "si", "sí", "yes", "true"}:
        return True
    if raw.strip().lower() in {"0", "no", "false", ""}:
        return False
    raise ValueError(_("cal indicar Sí o No"))


def _parse_family_csv(uploaded_file, academic_year):
    try:
        raw_bytes = uploaded_file.read()
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError(_("El CSV ha d'estar codificat en UTF-8."))
    digest = hashlib.sha256(raw_bytes).hexdigest()
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames != CSV_COLUMNS:
        raise ValueError(_("Les columnes no coincideixen amb la plantilla descarregable."))
    groups = {group.name.casefold(): group for group in CourseGroup.objects.filter(academic_year=academic_year)}
    diets = {diet.name.casefold(): diet for diet in Diet.objects.filter(active=True)}
    rows, errors, seen_students = [], [], set()
    for number, source in enumerate(reader, start=2):
        cleaned = {key: (value or "").strip() for key, value in source.items()}
        if not any(cleaned.values()):
            continue
        row_errors = []
        required = ("family_name", "student_first_name", "student_last_name")
        for column in required:
            if not cleaned[column]:
                row_errors.append(_("falta %(column)s") % {"column": column})
        course = groups.get(cleaned["course_group"].casefold()) if cleaned["course_group"] else None
        if cleaned["course_group"] and not course:
            row_errors.append(_("el grup no existeix en el curs seleccionat"))
        diet = diets.get(cleaned["default_diet"].casefold()) if cleaned["default_diet"] else None
        if cleaned["default_diet"] and not diet:
            row_errors.append(_("la dieta no existeix o no està activa"))
        try:
            birth_date = date.fromisoformat(cleaned["birth_date"]) if cleaned["birth_date"] else None
        except ValueError:
            birth_date = None
            row_errors.append(_("la data de naixement ha de tenir format AAAA-MM-DD"))
        try:
            scholarship = _csv_boolean(cleaned["scholarship"])
        except ValueError as error:
            scholarship = False
            row_errors.append(str(error))
        plan_values = {"fix": MealPlan.FIXED, "fixed": MealPlan.FIXED, "esporadic": MealPlan.SPORADIC, "esporàdic": MealPlan.SPORADIC, "sporadic": MealPlan.SPORADIC}
        meal_plan = plan_values.get(cleaned["meal_plan"].casefold(), MealPlan.FIXED)
        if cleaned["meal_plan"] and cleaned["meal_plan"].casefold() not in plan_values:
            row_errors.append(_("la modalitat ha de ser Fix o Esporàdic"))
        signature = (cleaned["family_name"].casefold(), cleaned["student_first_name"].casefold(), cleaned["student_last_name"].casefold())
        if signature in seen_students:
            row_errors.append(_("l'alumne es repeteix dins del fitxer"))
        seen_students.add(signature)
        if Student.objects.filter(family__name__iexact=cleaned["family_name"], first_name__iexact=cleaned["student_first_name"], last_name__iexact=cleaned["student_last_name"]).exists():
            row_errors.append(_("ja existeix un alumne amb aquesta família i nom"))
        if row_errors:
            errors.append({"row": number, "message": "; ".join(row_errors)})
            continue
        rows.append({
            **cleaned, "birth_date": birth_date.isoformat() if birth_date else "", "course_group_id": course.id if course else None,
            "diet_id": diet.id if diet else None, "scholarship_value": scholarship, "meal_plan_value": meal_plan,
        })
    return digest, rows, errors


@admin_required
def family_import(request):
    form = CSVImportForm(request.POST or None, request.FILES or None)
    batch = None
    if request.method == "POST" and form.is_valid():
        try:
            digest, rows, errors = _parse_family_csv(form.cleaned_data["csv_file"], form.cleaned_data["academic_year"])
        except ValueError as error:
            form.add_error("csv_file", str(error))
        else:
            batch = FamilyImportBatch.objects.create(
                academic_year=form.cleaned_data["academic_year"], uploaded_by=request.user, source_digest=digest,
                total_rows=len(rows) + len(errors), valid_rows=rows, errors=errors,
                expires_at=timezone.now() + timedelta(hours=2),
            )
            log_event(request.user, "family_import.previewed", batch, {"valid": len(rows), "errors": len(errors)})
            return redirect("cafeteria:family_import_preview", batch_id=batch.id)
    return render(request, "cafeteria/family_import.html", {"form": form, "csv_columns": CSV_COLUMNS})


@admin_required
def family_import_preview(request, batch_id):
    batch = get_object_or_404(FamilyImportBatch.objects.select_related("academic_year"), pk=batch_id)
    return render(request, "cafeteria/family_import_preview.html", {"batch": batch})


@admin_required
@require_POST
def family_import_confirm(request, batch_id):
    batch = get_object_or_404(FamilyImportBatch, pk=batch_id)
    if not batch.is_confirmable:
        messages.error(request, _("Aquesta previsualització ja no es pot importar. Torna a pujar el fitxer."))
        return redirect("cafeteria:family_import")
    created_families, created_students = 0, 0
    try:
        with transaction.atomic():
            family_cache = {}
            for row in batch.valid_rows:
                key = (row["family_name"].casefold(), row["billing_email"].casefold())
                family = family_cache.get(key)
                if family is None:
                    family = Family.objects.create(
                        name=row["family_name"], billing_email=row["billing_email"], phone=row["family_phone"],
                        address=row["family_address"],
                    )
                    family_cache[key] = family
                    created_families += 1
                Student.objects.create(
                    family=family, course_group_id=row["course_group_id"], first_name=row["student_first_name"],
                    last_name=row["student_last_name"], birth_date=row["birth_date"] or None,
                    contact_email=row["student_email"], contact_phone=row["student_phone"], contact_notes=row["contact_notes"],
                    default_diet_id=row["diet_id"], dietary_notes=row["dietary_notes"], is_scholarship=row["scholarship_value"],
                    meal_plan=row["meal_plan_value"],
                )
                created_students += 1
            batch.status = FamilyImportBatch.Status.IMPORTED
            batch.imported_at = timezone.now()
            batch.valid_rows = []  # discard the temporary personal data once it has been applied
            batch.save(update_fields=["status", "imported_at", "valid_rows"])
    except IntegrityError:
        messages.error(request, _("No s'ha pogut completar la importació perquè alguna dada ja existeix. No s'ha desat cap fila."))
        return redirect("cafeteria:family_import_preview", batch_id=batch.id)
    log_event(request.user, "family_import.confirmed", batch, {"families": created_families, "students": created_students})
    messages.success(request, _("Importació feta: %(families)s famílies i %(students)s alumnes. Ara pots enviar les invitacions manualment.") % {"families": created_families, "students": created_students})
    return redirect("cafeteria:people")


@admin_required
@require_GET
def family_import_template(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="plantilla-importacio-families.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(CSV_COLUMNS)
    writer.writerow(["Família exemple", "familia@example.org", "600000000", "Carrer Major, 1", "Laia", "Puig", "2019-03-12", "I4", "", "", "", "Ordinària", "", "No", "Fix"])
    return response
