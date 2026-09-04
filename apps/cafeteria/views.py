from __future__ import annotations

import calendar
import csv
import hashlib
from datetime import date, datetime, timedelta
from io import StringIO

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils import timezone
from django.utils.http import urlsafe_base64_encode
from django.utils import translation
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from .forms import (
    AcademicHolidayForm,
    AcademicYearForm,
    AfaFeeSettingsForm,
    AfaMembershipForm,
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
    PortalSettingsForm,
    TeacherMealProfileForm,
)
from .models import (
    AcademicHoliday,
    AcademicYear,
    AfaFeeSettings,
    AfaMembership,
    AfaMembershipStatus,
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
    MealType,
    MealSettings,
    MealPlan,
    MonthlyStatement,
    PortalSettings,
    PriceRule,
    Role,
    ServiceDay,
    StatementStatus,
    Student,
    TeacherMealBooking,
    TeacherMealProfile,
    TeacherMonthlyStatement,
    ensure_role_groups,
    log_event,
    user_has_role,
)
from .services import (
    bookings_for_day, is_service_day, is_tutor_locked,
    prepare_statements_for_month, reprice_open_bookings,
    teacher_bookings_for_day,
)
from .tasks import send_daily_report, send_monthly_statement, send_teacher_monthly_statement


def _is_staff(user):
    return user_has_role(user, Role.ADMIN, Role.MANAGER)


def _is_admin(user):
    return user_has_role(user, Role.ADMIN)


def _is_teacher(user):
    return user_has_role(user, Role.TEACHER)


def _ordinary_diet():
    diet, _created = Diet.objects.get_or_create(
        name="Ordinària", defaults={"description": "Dieta habitual", "active": True}
    )
    return diet


def _return_to_calendar(family_id, selected_dates, week_start=None):
    if week_start:
        try:
            selected_week = date.fromisoformat(week_start)
        except ValueError:
            selected_week = None
        if selected_week:
            return redirect(f"{reverse('cafeteria:family_calendar', args=[family_id])}?week={selected_week:%Y-%m-%d}")
    month = selected_dates[0].strftime("%Y-%m") if selected_dates else timezone.localdate().strftime("%Y-%m")
    return redirect(f"{reverse('cafeteria:family_calendar', args=[family_id])}?month={month}")


def _update_student_booking(*, actor, student, service_date, action, diet, reason=""):
    """Apply one requested meal without silently changing dates outside the service."""
    locked = is_tutor_locked(service_date)
    if locked and not _is_staff(actor):
        return False, "locked"
    if locked and _is_staff(actor) and not reason:
        return False, "reason_required"
    if not is_service_day(service_date, student):
        return False, "unavailable"
    booking = MealBooking.objects.filter(student=student, date=service_date).first()
    if action == "cancel":
        if not booking or booking.status != BookingStatus.ACTIVE:
            return False, "unchanged"
        booking.status = BookingStatus.CANCELLED
        booking.updated_by = actor
        booking.override_reason = reason
        booking.save(update_fields=["status", "updated_by", "override_reason", "updated_at"])
        log_event(actor, "booking.cancelled", booking, {"after_cutoff": locked, "reason": reason})
    else:
        selected_diet = diet or student.default_diet or _ordinary_diet()
        excursion = CourseClosure.objects.filter(course_group=student.course_group, date=service_date).exists()
        meal_type = MealType.PACKED_LUNCH if excursion else MealType.REGULAR
        if booking:
            booking.status = BookingStatus.ACTIVE
            booking.diet = selected_diet
            booking.diet_name = selected_diet.name
            booking.meal_type = meal_type
            booking.updated_by = actor
            booking.override_reason = reason
            booking.unit_price = PriceRule.amount_for(student, service_date)
            booking.save()
        else:
            booking = MealBooking.objects.create(
                student=student, date=service_date, diet=selected_diet,
                diet_name=selected_diet.name, meal_type=meal_type, created_by=actor,
                updated_by=actor, override_reason=reason,
            )
        log_event(actor, "booking.created_or_updated", booking, {
            "after_cutoff": locked, "reason": reason, "meal_type": meal_type,
        })
    DailyReport.objects.filter(date=service_date, sent_at__isnull=False).update(is_outdated=True)
    return True, "updated"


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
        active_bookings = bookings_for_day(today)
        staff_bookings = teacher_bookings_for_day(today)
        diets = {}
        for booking in list(active_bookings) + list(staff_bookings):
            name = _("Carmanyola") if booking.meal_type == MealType.PACKED_LUNCH else (booking.diet_name or _("Ordinària"))
            diets[name] = diets.get(name, 0) + 1
        context = {
            "is_staff": True,
            "today": today,
            "today_bookings": active_bookings[:12],
            "today_total": active_bookings.count() + staff_bookings.count(),
            "diet_totals": sorted(diets.items()),
            "pending_statements": MonthlyStatement.objects.filter(status=StatementStatus.PREPARED).count(),
            "outdated_reports": DailyReport.objects.filter(is_outdated=True).count(),
        }
        return render(request, "cafeteria/dashboard_staff.html", context)

    if _is_teacher(request.user):
        profile, _created = TeacherMealProfile.objects.get_or_create(
            user=request.user, defaults={"default_diet": _ordinary_diet()}
        )
        upcoming = TeacherMealBooking.objects.filter(
            teacher=profile, date__gte=today, status=BookingStatus.ACTIVE,
        ).select_related("diet").order_by("date")[:8]
        return render(request, "cafeteria/dashboard_teacher.html", {
            "profile": profile, "upcoming": upcoming, "today": today,
        })

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
    closures = {
        (closure.course_group_id, closure.date): closure
        for closure in CourseClosure.objects.filter(date__range=(month_start, month_end))
    }
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
                    "excursion": closures.get((student.course_group_id, day)),
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
                "excursion": closures.get((student.course_group_id, service_date)),
            })
        weekly_calendars.append({"student": student, "days": days})

    previous_month = (month_start.replace(day=1) - timedelta(days=1)).replace(day=1)
    next_month = (month_end + timedelta(days=1)).replace(day=1)
    today_change_notice = None
    today = timezone.localdate()
    meal_settings = MealSettings.objects.filter(
        academic_year__starts_on__lte=today,
        academic_year__ends_on__gte=today,
    ).first()
    if meal_settings and meal_settings.daily_cutoff and is_service_day(today):
        now = timezone.localtime()
        deadline = datetime.combine(today, meal_settings.daily_cutoff).replace(tzinfo=now.tzinfo)
        remaining_seconds = int((deadline - now).total_seconds())
        if remaining_seconds > 0:
            hours, remainder = divmod(remaining_seconds, 3600)
            minutes = remainder // 60
            today_change_notice = {
                "open": True,
                "hours": hours,
                "minutes": minutes,
                "cutoff": meal_settings.daily_cutoff,
            }
        else:
            today_change_notice = {"open": False, "cutoff": meal_settings.daily_cutoff}
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
        "has_siblings": len(students) > 1,
        "today_change_notice": today_change_notice if not _is_staff(request.user) else None,
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
        changed, result = _update_student_booking(
            actor=request.user, student=student, service_date=service_date,
            action=action, diet=diet, reason=reason,
        )
        if result == "reason_required":
            messages.error(request, _("Cal indicar el motiu de qualsevol canvi després de l'hora límit."))
            return _return_to_calendar(family.id, selected_dates)
        if changed:
            success += 1
        elif result != "unchanged":
            skipped += 1

    if success:
        messages.success(request, _("S'han actualitzat %(count)s dies de menjador.") % {"count": success})
    if skipped:
        messages.warning(request, _("S'han ignorat %(count)s dies no disponibles o bloquejats.") % {"count": skipped})
    return _return_to_calendar(family.id, selected_dates)


@require_POST
@login_required
def family_bulk_booking(request, family_id):
    """Joint weekly form: each child has independent days, with an optional copy action."""
    family = _family_for_user_or_404(request.user, family_id)
    students = list(family.students.filter(active=True).select_related("default_diet"))
    action = request.POST.get("action")
    reason = request.POST.get("override_reason", "").strip()
    date_sets = {}
    diets = {}
    for student in students:
        parsed = []
        for raw_date in request.POST.getlist(f"dates_{student.id}"):
            try:
                parsed.append(date.fromisoformat(raw_date))
            except ValueError:
                pass
        date_sets[student.id] = parsed
        diets[student.id] = Diet.objects.filter(pk=request.POST.get(f"diet_{student.id}"), active=True).first()
    source_id = request.POST.get("copy_from")
    if request.POST.get("copy_to_all") == "1" and source_id and source_id.isdigit() and int(source_id) in date_sets:
        source_dates, source_diet = date_sets[int(source_id)], diets[int(source_id)]
        for student in students:
            if student.id != int(source_id):
                date_sets[student.id], diets[student.id] = source_dates, source_diet

    success = skipped = 0
    all_dates = []
    for student in students:
        for service_date in date_sets[student.id]:
            all_dates.append(service_date)
            changed, result = _update_student_booking(
                actor=request.user, student=student, service_date=service_date, action=action,
                diet=diets[student.id], reason=reason,
            )
            if result == "reason_required":
                messages.error(request, _("Cal indicar el motiu de qualsevol canvi després de l'hora límit."))
                return _return_to_calendar(family.id, all_dates, request.POST.get("return_week"))
            if changed:
                success += 1
            elif result != "unchanged":
                skipped += 1
    if success:
        messages.success(request, _("S'han actualitzat %(count)s reserves de menjador.") % {"count": success})
    if skipped:
        messages.warning(request, _("S'han ignorat %(count)s dies no disponibles o bloquejats.") % {"count": skipped})
    if not all_dates:
        messages.info(request, _("Selecciona com a mínim un dia abans d'escollir una acció."))
    return _return_to_calendar(family.id, all_dates, request.POST.get("return_week"))


@login_required
def teacher_calendar(request):
    if not _is_teacher(request.user):
        return HttpResponseForbidden(_("Aquesta pàgina és per al personal docent."))
    profile, _created = TeacherMealProfile.objects.get_or_create(
        user=request.user, defaults={"default_diet": _ordinary_diet()}
    )
    try:
        week_start = date.fromisoformat(request.GET.get("week", ""))
        week_start -= timedelta(days=week_start.weekday())
    except ValueError:
        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday())
    bookings = {booking.date: booking for booking in TeacherMealBooking.objects.filter(
        teacher=profile, date__range=(week_start, week_start + timedelta(days=4))
    ).select_related("diet")}
    days = [
        {
            "date": week_start + timedelta(days=offset),
            "available": is_service_day(week_start + timedelta(days=offset)),
            "booking": bookings.get(week_start + timedelta(days=offset)),
            "locked": is_tutor_locked(week_start + timedelta(days=offset)),
        }
        for offset in range(5)
    ]
    return render(request, "cafeteria/teacher_calendar.html", {
        "profile": profile, "days": days, "diets": Diet.objects.filter(active=True),
        "week_start": week_start, "previous_week": week_start - timedelta(days=7),
        "next_week": week_start + timedelta(days=7),
    })


@require_POST
@login_required
def teacher_bulk_booking(request):
    if not _is_teacher(request.user):
        return HttpResponseForbidden(_("No tens permís per modificar aquestes reserves."))
    profile, _created = TeacherMealProfile.objects.get_or_create(
        user=request.user, defaults={"default_diet": _ordinary_diet()}
    )
    selected_dates = []
    for raw_date in request.POST.getlist("dates"):
        try:
            selected_dates.append(date.fromisoformat(raw_date))
        except ValueError:
            pass
    action = request.POST.get("action")
    diet = Diet.objects.filter(pk=request.POST.get("diet_id"), active=True).first() or profile.default_diet
    updated = skipped = 0
    for service_date in selected_dates:
        if is_tutor_locked(service_date) or not is_service_day(service_date):
            skipped += 1
            continue
        booking = TeacherMealBooking.objects.filter(teacher=profile, date=service_date).first()
        if action == "cancel":
            if booking and booking.status == BookingStatus.ACTIVE:
                booking.status = BookingStatus.CANCELLED
                booking.updated_by = request.user
                booking.save(update_fields=["status", "updated_by", "updated_at"])
                updated += 1
        elif booking:
            booking.status = BookingStatus.ACTIVE
            booking.diet = diet
            booking.diet_name = diet.name
            booking.meal_type = MealType.REGULAR
            booking.updated_by = request.user
            booking.unit_price = PriceRule.amount_for_category(False, profile.meal_plan, service_date)
            booking.save()
            updated += 1
        else:
            TeacherMealBooking.objects.create(
                teacher=profile, date=service_date, diet=diet, diet_name=diet.name,
                created_by=request.user, updated_by=request.user,
            )
            updated += 1
        DailyReport.objects.filter(date=service_date, sent_at__isnull=False).update(is_outdated=True)
    if updated:
        messages.success(request, _("S'han actualitzat %(count)s reserves.") % {"count": updated})
    if skipped:
        messages.warning(request, _("Alguns dies no es poden modificar perquè no hi ha servei o ja s'ha tancat el termini."))
    target = selected_dates[0].strftime("%Y-%m-%d") if selected_dates else timezone.localdate().strftime("%Y-%m-%d")
    return redirect(f"{reverse('cafeteria:teacher_calendar')}?week={target}")


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
        invitation_path = reverse("cafeteria:invitation_accept", args=[invitation.token])
        invitation_url = f"{settings.APP_BASE_URL}{invitation_path}" if settings.APP_BASE_URL else request.build_absolute_uri(invitation_path)
        if not settings.EMAIL_HOST:
            messages.info(request, _("La invitació s'ha creat. Copia l'enllaç i comparteix-lo manualment perquè la persona pugui crear la contrasenya."))
        else:
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
                messages.success(request, _("S'ha enviat la invitació. També en pots copiar l'enllaç."))
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
    if invitation.role == Role.TEACHER:
        TeacherMealProfile.objects.get_or_create(user=user, defaults={"default_diet": _ordinary_diet()})
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
    try:
        selected_date = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        selected_date = timezone.localdate()
    student_bookings = bookings_for_day(selected_date)
    teacher_bookings = teacher_bookings_for_day(selected_date)
    diet_totals = {}
    for booking in list(student_bookings) + list(teacher_bookings):
        name = _("Carmanyola") if booking.meal_type == MealType.PACKED_LUNCH else (booking.diet_name or _("Ordinària"))
        diet_totals[name] = diet_totals.get(name, 0) + 1
    reports = DailyReport.objects.all()[:50]
    return render(request, "cafeteria/daily_reports.html", {
        "reports": reports, "today": timezone.localdate(), "selected_date": selected_date,
        "student_bookings": student_bookings, "teacher_bookings": teacher_bookings,
        "diet_totals": sorted(diet_totals.items()),
        "daily_total": student_bookings.count() + teacher_bookings.count(),
    })


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
    return redirect(f"{reverse('cafeteria:daily_reports')}?date={report_date.isoformat()}")


@staff_required
def monthly_planning(request):
    try:
        month_start = datetime.strptime(request.GET.get("month", ""), "%Y-%m").date().replace(day=1)
    except ValueError:
        month_start = timezone.localdate().replace(day=1)
    month_end = month_start.replace(day=calendar.monthrange(month_start.year, month_start.month)[1])
    student_bookings = MealBooking.objects.filter(
        date__range=(month_start, month_end), status=BookingStatus.ACTIVE,
    ).select_related("student", "diet")
    teacher_bookings = TeacherMealBooking.objects.filter(
        date__range=(month_start, month_end), status=BookingStatus.ACTIVE,
    ).select_related("teacher__user", "diet")
    grouped = {}
    for booking in student_bookings:
        day = grouped.setdefault(booking.date, {"students": [], "teachers": [], "diets": {}, "packed": 0})
        day["students"].append(booking)
        label = _("Carmanyola") if booking.meal_type == MealType.PACKED_LUNCH else (booking.diet_name or _("Ordinària"))
        day["diets"][label] = day["diets"].get(label, 0) + 1
        day["packed"] += booking.meal_type == MealType.PACKED_LUNCH
    for booking in teacher_bookings:
        day = grouped.setdefault(booking.date, {"students": [], "teachers": [], "diets": {}, "packed": 0})
        day["teachers"].append(booking)
        label = _("Carmanyola") if booking.meal_type == MealType.PACKED_LUNCH else (booking.diet_name or _("Ordinària"))
        day["diets"][label] = day["diets"].get(label, 0) + 1
        day["packed"] += booking.meal_type == MealType.PACKED_LUNCH
    days = []
    for offset in range((month_end - month_start).days + 1):
        current = month_start + timedelta(days=offset)
        data = grouped.get(current, {"students": [], "teachers": [], "diets": {}, "packed": 0})
        if is_service_day(current) or data["students"] or data["teachers"]:
            days.append({"date": current, **data, "total": len(data["students"]) + len(data["teachers"])})
    return render(request, "cafeteria/monthly_planning.html", {
        "month_start": month_start,
        "previous_month": (month_start - timedelta(days=1)).replace(day=1),
        "next_month": (month_end + timedelta(days=1)).replace(day=1), "days": days,
    })


def _statement_is_visible_to_user(statement, user):
    return user.is_superuser or _is_staff(user) or statement.family.memberships.filter(user=user).exists()


@login_required
def monthly_statements(request):
    statements = MonthlyStatement.objects.select_related("family")
    teacher_statements = TeacherMonthlyStatement.objects.select_related("teacher__user")
    if _is_teacher(request.user) and not _is_staff(request.user):
        statements = statements.none()
        teacher_statements = teacher_statements.filter(teacher__user=request.user)
    elif not _is_staff(request.user):
        statements = statements.filter(family__memberships__user=request.user)
        teacher_statements = teacher_statements.none()
    return render(request, "cafeteria/monthly_statements.html", {
        "statements": statements[:100], "teacher_statements": teacher_statements[:100],
        "is_staff": _is_staff(request.user), "is_teacher": _is_teacher(request.user),
    })


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
    count = prepare_statements_for_month(year, month)
    messages.success(request, _("S'han preparat %(count)s resums mensuals.") % {"count": count})
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
        diet = "Carmanyola" if line.meal_type == MealType.PACKED_LUNCH else line.diet_name
        writer.writerow([line.service_date.isoformat(), line.student.full_name, diet, line.get_meal_plan_display(), "Sí" if line.scholarship else "No", line.unit_price])
    writer.writerow([])
    writer.writerow(["Total", "", "", "", "", statement.total])
    return response


def _teacher_statement_is_visible_to_user(statement, user):
    return user.is_superuser or _is_staff(user) or statement.teacher.user_id == user.id


@login_required
def teacher_statement_detail(request, statement_id):
    statement = get_object_or_404(TeacherMonthlyStatement.objects.select_related("teacher__user"), pk=statement_id)
    if not _teacher_statement_is_visible_to_user(statement, request.user):
        return HttpResponseForbidden(_("No tens permís per veure aquest resum."))
    return render(request, "cafeteria/teacher_statement_detail.html", {"statement": statement, "is_staff": _is_staff(request.user)})


@staff_required
@require_POST
def teacher_statement_close(request, statement_id):
    statement = get_object_or_404(TeacherMonthlyStatement, pk=statement_id)
    if statement.status == StatementStatus.PREPARED:
        statement.status = StatementStatus.CLOSED
        statement.closed_at = timezone.now()
        statement.closed_by = request.user
        statement.save(update_fields=["status", "closed_at", "closed_by"])
    return redirect("cafeteria:teacher_statement_detail", statement_id=statement.id)


@staff_required
@require_POST
def teacher_statement_send(request, statement_id):
    statement = get_object_or_404(TeacherMonthlyStatement, pk=statement_id)
    if statement.status == StatementStatus.PREPARED:
        messages.error(request, _("Cal tancar el resum abans d'enviar-lo."))
    elif not settings.EMAIL_HOST:
        messages.warning(request, _("No hi ha SMTP configurat. Pots descarregar o consultar el resum des del portal."))
    else:
        try:
            sent = send_teacher_monthly_statement(statement.id, request.user.id)
        except Exception:
            messages.error(request, _("No s'ha pogut enviar el resum. Revisa la configuració SMTP."))
        else:
            messages.success(request, _("S'ha enviat el resum.") if sent else _("La persona no té cap correu configurat."))
    return redirect("cafeteria:teacher_statement_detail", statement_id=statement.id)


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


def password_reset_request(request):
    """Password reset that remains safe and usable before SMTP is configured."""
    form = PasswordResetForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if settings.EMAIL_HOST:
            try:
                form.save(
                    request=request,
                    use_https=request.is_secure(),
                    email_template_name="registration/password_reset_email.txt",
                    subject_template_name="registration/password_reset_subject.txt",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                )
            except Exception:
                messages.warning(request, _("No s'ha pogut enviar el correu de recuperació. Torna-ho a provar o contacta amb ARAM (lleure@aramemporda.com)."))
        else:
            messages.info(request, _("La recuperació per correu encara no està configurada. Demana a l'administració un enllaç personal de restauració."))
        return redirect("cafeteria:password_reset_done")
    return render(request, "registration/password_reset_form.html", {"form": form, "smtp_configured": bool(settings.EMAIL_HOST)})


def _password_reset_url(request, user):
    path = reverse("cafeteria:password_reset_confirm", args=[
        urlsafe_base64_encode(force_bytes(user.pk)), default_token_generator.make_token(user),
    ])
    return f"{settings.APP_BASE_URL}{path}" if settings.APP_BASE_URL else request.build_absolute_uri(path)


def _accounts_context(request):
    query = request.GET.get("q", "").strip()
    users = User.objects.select_related("profile", "teacher_meal_profile").prefetch_related("groups", "family_memberships__family")
    if query:
        users = users.filter(
            Q(email__icontains=query) | Q(first_name__icontains=query) |
            Q(last_name__icontains=query)
        )
    return {"accounts": users.order_by("first_name", "last_name", "email")[:250], "query": query}


@admin_required
def accounts(request):
    return render(request, "cafeteria/accounts.html", _accounts_context(request))


@admin_required
@require_POST
def account_reset_link(request, user_id):
    account = get_object_or_404(User, pk=user_id, is_active=True)
    reset_url = _password_reset_url(request, account)
    context = _accounts_context(request)
    context.update({"reset_url": reset_url, "reset_account": account})
    if settings.EMAIL_HOST and account.email:
        try:
            PasswordResetForm({"email": account.email}).save(
                request=request, use_https=request.is_secure(),
                email_template_name="registration/password_reset_email.txt",
                subject_template_name="registration/password_reset_subject.txt",
                from_email=settings.DEFAULT_FROM_EMAIL,
            )
            messages.success(request, _("S'ha enviat l'enllaç de restauració a %(email)s.") % {"email": account.email})
        except Exception:
            messages.warning(request, _("No s'ha pogut enviar el correu. Copia l'enllaç manualment."))
    else:
        messages.info(request, _("Copia l'enllaç i comparteix-lo de manera segura amb la persona registrada."))
    log_event(request.user, "account.reset_link_created", account, {"email": account.email})
    return render(request, "cafeteria/accounts.html", context)


@admin_required
def teacher_profile_edit(request, profile_id):
    profile = get_object_or_404(TeacherMealProfile.objects.select_related("user"), pk=profile_id)
    form = TeacherMealProfileForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        reprice_open_bookings()
        log_event(request.user, "teacher_meal_profile.updated", saved)
        messages.success(request, _("S'ha actualitzat el perfil de menjador."))
        return redirect("cafeteria:accounts")
    return render(request, "cafeteria/entity_form.html", {
        "form": form,
        "title": _("Perfil de menjador de %(name)s") % {"name": profile.full_name},
        "back_url": reverse("cafeteria:accounts"),
    })


@staff_required
def menu_settings(request):
    portal, _created = PortalSettings.objects.get_or_create()
    form = PortalSettingsForm(request.POST or None, instance=portal)
    if request.method == "POST" and form.is_valid():
        portal = form.save(commit=False)
        portal.updated_by = request.user
        portal.save()
        log_event(request.user, "portal.menu_url_updated", portal)
        messages.success(request, _("S'ha actualitzat l'enllaç al menú de l'escola."))
        return redirect("cafeteria:menu_settings")
    return render(request, "cafeteria/menu_settings.html", {"form": form})


def _active_year_or_none():
    return AcademicYear.objects.filter(is_active=True).first() or AcademicYear.objects.first()


@admin_required
def management_dashboard(request):
    active_year = _active_year_or_none()
    today = timezone.localdate()
    active_memberships = AfaMembership.objects.filter(academic_year=active_year) if active_year else AfaMembership.objects.none()
    return render(request, "cafeteria/management_dashboard.html", {
        "active_year": active_year,
        "family_count": Family.objects.filter(active=True).count(),
        "student_count": Student.objects.filter(active=True).count(),
        "teacher_count": TeacherMealProfile.objects.filter(active=True).count(),
        "afa_member_count": active_memberships.count(),
        "afa_pending_count": active_memberships.filter(status=AfaMembershipStatus.PENDING).count(),
        "pending_invitations": Invitation.objects.filter(accepted_at__isnull=True, expires_at__gt=timezone.now()).count(),
        "service_days": ServiceDay.objects.filter(academic_year=active_year, is_service_day=True).count() if active_year else 0,
        "holiday_count": AcademicHoliday.objects.filter(academic_year=active_year).count() if active_year else 0,
        "today_total": MealBooking.objects.filter(date=today, status=BookingStatus.ACTIVE).count(),
        "recent_imports": FamilyImportBatch.objects.select_related("academic_year", "uploaded_by")[:5],
    })


@staff_required
def dining_dashboard(request):
    today = timezone.localdate()
    return render(request, "cafeteria/dining_dashboard.html", {
        "today": today,
        "today_total": MealBooking.objects.filter(date=today, status=BookingStatus.ACTIVE).count() + TeacherMealBooking.objects.filter(date=today, status=BookingStatus.ACTIVE).count(),
        "prepared_statements": MonthlyStatement.objects.filter(status=StatementStatus.PREPARED).count() + TeacherMonthlyStatement.objects.filter(status=StatementStatus.PREPARED).count(),
        "outdated_reports": DailyReport.objects.filter(is_outdated=True).count(),
    })


@admin_required
def contacts_dashboard(request):
    active_year = _active_year_or_none()
    memberships = AfaMembership.objects.filter(academic_year=active_year) if active_year else AfaMembership.objects.none()
    return render(request, "cafeteria/contacts_dashboard.html", {
        "active_year": active_year,
        "family_count": Family.objects.filter(active=True).count(),
        "student_count": Student.objects.filter(active=True).count(),
        "teacher_count": TeacherMealProfile.objects.filter(active=True).count(),
        "member_count": memberships.count(),
        "pending_fee_count": memberships.filter(status=AfaMembershipStatus.PENDING).count(),
    })


@admin_required
def academic_dashboard(request):
    active_year = _active_year_or_none()
    return render(request, "cafeteria/academic_dashboard.html", {
        "active_year": active_year,
        "course_group_count": CourseGroup.objects.filter(academic_year=active_year).count() if active_year else 0,
        "service_day_count": ServiceDay.objects.filter(academic_year=active_year, is_service_day=True).count() if active_year else 0,
        "holiday_count": AcademicHoliday.objects.filter(academic_year=active_year).count() if active_year else 0,
        "excursion_count": CourseClosure.objects.filter(course_group__academic_year=active_year).count() if active_year else 0,
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
    active_year = _active_year_or_none()
    visible_families = list(families[:100])
    membership_map = {
        membership.family_id: membership
        for membership in AfaMembership.objects.filter(academic_year=active_year, family__in=visible_families)
    } if active_year else {}
    return render(request, "cafeteria/people.html", {
        "family_rows": [{"family": family, "membership": membership_map.get(family.id)} for family in visible_families],
        "students": students.order_by("family__name", "first_name")[:150],
        "teachers": TeacherMealProfile.objects.select_related("user", "default_diet").filter(
            Q(user__first_name__icontains=query) | Q(user__last_name__icontains=query) | Q(user__email__icontains=query)
        )[:100] if query else TeacherMealProfile.objects.select_related("user", "default_diet")[:100],
        "query": query,
        "active_year": active_year,
    })


@admin_required
def afa_memberships(request):
    selected_id = request.GET.get("year")
    academic_year = AcademicYear.objects.filter(pk=selected_id).first() if selected_id else _active_year_or_none()
    if not academic_year:
        messages.info(request, _("Primer crea un curs acadèmic per gestionar les quotes AFA."))
        return redirect("cafeteria:academic_dashboard")
    fee_settings, _created = AfaFeeSettings.objects.get_or_create(academic_year=academic_year)
    fee_form = AfaFeeSettingsForm(request.POST or None, instance=fee_settings, prefix="fee")
    if request.method == "POST" and request.POST.get("intent") == "fee" and fee_form.is_valid():
        saved = fee_form.save(commit=False)
        saved.updated_by = request.user
        saved.save()
        log_event(request.user, "afa_fee_settings.updated", saved, {"amount": str(saved.amount)})
        messages.success(request, _("S'ha actualitzat la quota AFA de referència."))
        return redirect(f"{reverse('cafeteria:afa_memberships')}?year={academic_year.id}")
    status = request.GET.get("status")
    all_memberships = AfaMembership.objects.filter(academic_year=academic_year).select_related("family")
    memberships = all_memberships
    if status in AfaMembershipStatus.values:
        memberships = memberships.filter(status=status)
    member_family_ids = all_memberships.values_list("family_id", flat=True)
    non_member_families = Family.objects.filter(active=True).exclude(pk__in=member_family_ids)
    return render(request, "cafeteria/afa_memberships.html", {
        "academic_year": academic_year,
        "years": AcademicYear.objects.all(),
        "fee_form": fee_form,
        "fee_settings": fee_settings,
        "memberships": memberships,
        "non_member_families": non_member_families[:100],
        "selected_status": status,
    })


@admin_required
def afa_membership_edit(request, family_id):
    family = get_object_or_404(Family, pk=family_id)
    selected_id = request.GET.get("year") or request.POST.get("year")
    academic_year = AcademicYear.objects.filter(pk=selected_id).first() if selected_id else _active_year_or_none()
    if not academic_year:
        messages.error(request, _("Cal crear un curs acadèmic abans de registrar una quota AFA."))
        return redirect("cafeteria:academic_dashboard")
    fee_settings, _created = AfaFeeSettings.objects.get_or_create(academic_year=academic_year)
    membership = AfaMembership.objects.filter(family=family, academic_year=academic_year).first()
    form = AfaMembershipForm(
        request.POST or None,
        instance=membership,
        initial={"amount": fee_settings.amount} if membership is None else None,
    )
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        saved.family = family
        saved.academic_year = academic_year
        saved.updated_by = request.user
        saved.save()
        log_event(request.user, "afa_membership.created" if membership is None else "afa_membership.updated", saved)
        messages.success(request, _("S'ha desat la quota AFA de la família."))
        return redirect(f"{reverse('cafeteria:afa_memberships')}?year={academic_year.id}")
    return render(request, "cafeteria/entity_form.html", {
        "form": form,
        "title": _("Quota AFA de %(family)s") % {"family": family.name},
        "back_url": f"{reverse('cafeteria:afa_memberships')}?year={academic_year.id}",
        "help_text": _("Aquesta quota no afecta les reserves ni els imports del menjador."),
    })


@admin_required
@require_POST
def afa_membership_delete(request, membership_id):
    membership = get_object_or_404(AfaMembership, pk=membership_id)
    academic_year_id = membership.academic_year_id
    log_event(request.user, "afa_membership.deleted", membership)
    membership.delete()
    messages.success(request, _("La família consta com a no sòcia en aquest curs."))
    return redirect(f"{reverse('cafeteria:afa_memberships')}?year={academic_year_id}")


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
    holidays = AcademicHoliday.objects.filter(
        academic_year=year,
        starts_on__lte=month_end,
        ends_on__gte=month_start,
    )
    holiday_dates = set()
    for holiday in holidays:
        current_day = max(holiday.starts_on, month_start)
        final_day = min(holiday.ends_on, month_end)
        while current_day <= final_day:
            holiday_dates.add(current_day)
            current_day += timedelta(days=1)
    previous_month = (month_start - timedelta(days=1)).replace(day=1)
    next_month = (month_end + timedelta(days=1)).replace(day=1)
    return render(request, "cafeteria/school_calendar.html", {
        "year": year, "years": AcademicYear.objects.all(), "month_start": month_start,
        "weeks": calendar.Calendar(firstweekday=0).monthdatescalendar(month_start.year, month_start.month),
        "service_days": service_days, "closures": closures, "holidays": holidays,
        "holiday_dates": holiday_dates,
        "all_holidays": AcademicHoliday.objects.filter(academic_year=year),
        "previous_month": previous_month, "next_month": next_month,
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
        AfaFeeSettings.objects.get_or_create(academic_year=saved)
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
        messages.success(request, _("S'ha registrat l'excursió. Les reserves es mantenen i, si cal, es mostraran com a carmanyola."))
    else:
        messages.error(request, _("No s'ha pogut desar l'excursió."))
    return redirect(request.POST.get("next") or "cafeteria:school_calendar")


@admin_required
def academic_holiday_form(request, holiday_id=None):
    holiday = get_object_or_404(AcademicHoliday, pk=holiday_id) if holiday_id else None
    selected_id = request.GET.get("year")
    selected_year = AcademicYear.objects.filter(pk=selected_id).first() if selected_id else _active_year_or_none()
    form = AcademicHolidayForm(
        request.POST or None,
        instance=holiday,
        initial={"academic_year": selected_year, "starts_on": selected_year.starts_on if selected_year else None} if holiday is None else None,
    )
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        log_event(request.user, "academic_holiday.created" if holiday is None else "academic_holiday.updated", saved)
        messages.success(request, _("S'ha desat el període festiu."))
        return redirect(f"{reverse('cafeteria:school_calendar')}?year={saved.academic_year_id}&month={saved.starts_on:%Y-%m}")
    return render(request, "cafeteria/entity_form.html", {
        "form": form,
        "title": _("Nou festiu acadèmic") if holiday is None else _("Edita el festiu acadèmic"),
        "back_url": reverse("cafeteria:school_calendar"),
        "help_text": _("Els festius generals, locals i de centre tanquen el servei de menjador durant tot el període."),
    })


@admin_required
@require_POST
def academic_holiday_delete(request, holiday_id):
    holiday = get_object_or_404(AcademicHoliday, pk=holiday_id)
    year_id, starts_on = holiday.academic_year_id, holiday.starts_on
    log_event(request.user, "academic_holiday.deleted", holiday)
    holiday.delete()
    messages.success(request, _("S'ha eliminat el període festiu. Les reserves anul·lades no es reactiven automàticament."))
    return redirect(f"{reverse('cafeteria:school_calendar')}?year={year_id}&month={starts_on:%Y-%m}")


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
        diet = diets.get(cleaned["default_diet"].casefold()) if cleaned["default_diet"] else _ordinary_diet()
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
