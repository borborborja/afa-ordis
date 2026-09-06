from __future__ import annotations

import calendar
import csv
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import zipfile
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path
from functools import wraps
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.contrib.sessions.models import Session
from django.core.mail import send_mail
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import Count, F, Q, Sum
from django.forms import formset_factory
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_encode
from django.utils import translation
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from .forms import (
    AcademicHolidayForm,
    AcademicIntensivePeriodForm,
    AcademicNoticeForm,
    AcademicYearForm,
    AllergyReviewForm,
    AfaFeeSettingsForm,
    AfaMembershipForm,
    CourseClosureForm,
    CourseGroupForm,
    CSVImportForm,
    DailyReportRecipientForm,
    DietForm,
    EconomicCategoryForm,
    EconomicEntryForm,
    EconomicSettingsForm,
    EconomicSubmissionForm,
    FamilyContactForm,
    FamilyForm,
    FamilyOnboardingContactForm,
    FamilyStudentCreateForm,
    FinancialAccountForm,
    FamilyBookingPreferenceForm,
    InvitationAcceptanceForm,
    InvitationForm,
    MealSettingsForm,
    PriceRuleForm,
    StaffStudentForm,
    TutorStudentForm,
    PortalSettingsForm,
    PortalFamilyRegistrationSettingsForm,
    TeacherMealProfileForm,
)
from .identity import normalize_email
from .models import (
    AcademicHoliday,
    AcademicIntensivePeriod,
    AcademicNotice,
    AcademicYear,
    AllergyReviewStatus,
    AfaFeeSettings,
    AfaMembership,
    AfaMembershipStatus,
    AuditEvent,
    BookingStatus,
    CourseClosure,
    CourseGroup,
    DailyReport,
    Diet,
    EconomicAttachment,
    EconomicCategory,
    EconomicEntry,
    EconomicEntryType,
    EconomicPaymentStatus,
    EconomicReviewStatus,
    EconomicSettings,
    Family,
    FamilyBookingView,
    FamilyImportBatch,
    FamilyMembership,
    FinancialAccount,
    Invitation,
    MealBooking,
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
    UserProfile,
    ensure_role_groups,
    log_event,
    user_has_role,
)
from .services import (
    bookings_for_day, is_service_day, is_tutor_locked,
    prepare_statements_for_month, reprice_open_bookings,
    teacher_bookings_for_day,
    prepare_monthly_statement, prepare_teacher_monthly_statement, service_calendar,
)
from .http import atomic_write, csv_cell, local_redirect, positive_pk
from .auth import consume_attempt
from .database import dbapi as sqlite3, connect as database_connect
from .privacy import medical_access, explicit_role, has_health_consent
from .tasks import send_daily_report, send_monthly_statement, send_teacher_monthly_statement


DATABASE_RESTORE_LOCK = threading.Lock()
logger = logging.getLogger(__name__)
RESTORE_CONFIRMATION = "RESTAURA"
RESTORE_SAFETY_WINDOW_SECONDS = 15 * 60
MAX_BACKUP_UPLOAD_BYTES = 100 * 1024 * 1024


@require_GET
def web_app_manifest(request):
    """Install metadata follows the active language URL without caching private pages."""
    start_url = reverse("cafeteria:dashboard")
    response = JsonResponse({
        "id": start_url,
        "name": _("Portal AFA Ordis"),
        "short_name": "AFA Ordis",
        "description": _("Portal de gestió de l'AFA d'Ordis"),
        "lang": translation.get_language() or "ca",
        "start_url": start_url,
        "scope": start_url,
        "display": "standalone",
        "background_color": "#f6f7f2",
        "theme_color": "#185c51",
        "icons": [
            {
                "src": static("cafeteria/images/pwa-logo-escola-192.png"),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": static("cafeteria/images/pwa-logo-escola-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable",
            },
        ],
    }, content_type="application/manifest+json")
    response["Cache-Control"] = "no-cache"
    return response


@require_GET
def web_app_service_worker(request):
    """Cache only static public assets: application and family data always stays online."""
    assets = [
        static("cafeteria/style.css"),
        static("cafeteria/portal.js"),
        static("cafeteria/images/pwa-logo-escola-192.png"),
        static("cafeteria/images/pwa-logo-escola-512.png"),
    ]
    version = hashlib.sha256(json.dumps(assets).encode()).hexdigest()[:16]
    script = """const CACHE = 'afa-ordis-static-%s';
const ASSETS = %s;
self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(ASSETS)));
  self.skipWaiting();
});
self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key.startsWith('afa-ordis-static-') && key !== CACHE).map((key) => caches.delete(key)))));
  self.clients.claim();
});
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || !url.pathname.startsWith('/static/')) return;
  event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request).then((response) => {
    if (!response || !response.ok) return response;
    const copy = response.clone();
    caches.open(CACHE).then((cache) => cache.put(event.request, copy));
    return response;
  })));
});
""" % (version, json.dumps(assets))
    response = HttpResponse(script, content_type="application/javascript; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    response["Service-Worker-Allowed"] = "/"
    return response


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
            selected_month = date.fromisoformat(week_start)
        except ValueError:
            selected_month = None
        if selected_month:
            return redirect(f"{reverse('cafeteria:family_calendar', args=[family_id])}?month={selected_month:%Y-%m}")
    month = selected_dates[0].strftime("%Y-%m") if selected_dates else timezone.localdate().strftime("%Y-%m")
    return redirect(f"{reverse('cafeteria:family_calendar', args=[family_id])}?month={month}")


@transaction.atomic
def _update_student_booking(*, actor, student, service_date, action, diet, reason=""):
    """Apply one requested meal without silently changing dates outside the service."""
    if _pending_family_onboarding(actor, student.family_id):
        return False, "profile_required"
    if action not in {"add", "cancel"}:
        return False, "invalid"
    if action == "add" and student.meal_safety_hold:
        return False, "profile_required"
    if MonthlyStatement.objects.filter(family_id=student.family_id, year=service_date.year, month=service_date.month).exclude(status=StatementStatus.PREPARED).exists():
        return False, "locked"
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
        if booking:
            booking.status = BookingStatus.ACTIVE
            booking.diet = selected_diet
            booking.diet_name = selected_diet.name
            booking.updated_by = actor
            booking.override_reason = reason
            booking.unit_price = PriceRule.amount_for(student, service_date)
            booking.save()
        else:
            booking = MealBooking.objects.create(
                student=student, date=service_date, diet=selected_diet,
                diet_name=selected_diet.name, created_by=actor,
                updated_by=actor, override_reason=reason,
            )
        log_event(actor, "booking.created_or_updated", booking, {
            "after_cutoff": locked, "reason": reason,
        })
    DailyReport.objects.filter(date=service_date, sent_at__isnull=False).update(is_outdated=True)
    return True, "updated"


def staff_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not _is_staff(request.user):
            return HttpResponseForbidden(_("No tens permís per accedir a aquesta pàgina."))
        return view(request, *args, **kwargs)
    return wrapped


def admin_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not _is_admin(request.user):
            return HttpResponseForbidden(_("No tens permís per accedir a aquesta pàgina."))
        return view(request, *args, **kwargs)
    return wrapped


def _family_for_user_or_404(user, family_id):
    if user.is_superuser or _is_admin(user):
        return get_object_or_404(Family, pk=family_id)
    return get_object_or_404(Family, pk=family_id, memberships__user=user, active=True)


def _pending_family_onboarding(user, family_id=None):
    """Return the required initial setup for a tutor, if one is still open."""
    portal = PortalSettings.objects.first()
    if not user.is_authenticated or _is_staff(user) or not (portal and portal.allow_family_student_creation):
        return None
    memberships = FamilyMembership.objects.filter(
        user=user,
        onboarding_required=True,
        onboarding_completed_at__isnull=True,
        family__active=True,
    )
    if family_id is not None:
        memberships = memberships.filter(family_id=family_id)
    for membership in memberships.select_related("family").order_by("family__name"):
        if not membership.family.students.filter(active=True).exists():
            return membership
    return None


def _student_profile_needs_completion(student):
    return not student.birth_date or student.has_allergy is None


def _family_profile_needs_completion(family):
    return bool(
        not family.phone
        or any(_student_profile_needs_completion(student) for student in family.students.filter(active=True))
    )


def _staff_dashboard_widgets(user):
    """Return the selectable, role-appropriate widgets for the personal home page."""
    today = timezone.localdate()
    today_bookings = list(bookings_for_day(today)) + list(teacher_bookings_for_day(today))
    diet_totals = {}
    for booking in today_bookings:
        diet_name = booking.diet_name or _("Ordinària")
        diet_totals[diet_name] = diet_totals.get(diet_name, 0) + 1
    widget_map = {
        "today_meals": {
            "key": "today_meals", "icon": "meal", "label": _("Àpats d'avui"),
            "value": len(today_bookings), "description": _("reserves actives previstes"),
            "href": reverse("cafeteria:daily_reports"),
        },
        "diet_summary": {
            "key": "diet_summary", "icon": "chart", "label": _("Dietes d'avui"),
            "value": _("%(count)d tipus") % {"count": len(diet_totals)},
            "description": " · ".join(f"{name}: {count}" for name, count in sorted(diet_totals.items())) or _("Sense reserves"),
            "href": reverse("cafeteria:daily_reports"),
        },
        "daily_reports": {
            "key": "daily_reports", "icon": "list", "label": _("Llistats per actualitzar"),
            "value": DailyReport.objects.filter(is_outdated=True).count(),
            "description": _("llistats enviats amb canvis posteriors"),
            "href": reverse("cafeteria:daily_reports"),
        },
        "statements": {
            "key": "statements", "icon": "document", "label": _("Resums pendents"),
            "value": MonthlyStatement.objects.filter(status=StatementStatus.PREPARED).count() + TeacherMonthlyStatement.objects.filter(status=StatementStatus.PREPARED).count(),
            "description": _("resums mensuals per revisar"),
            "href": reverse("cafeteria:monthly_statements"),
        },
        "monthly_plan": {
            "key": "monthly_plan", "icon": "calendar", "label": _("Planificació"),
            "value": MealBooking.objects.filter(date__year=today.year, date__month=today.month, status=BookingStatus.ACTIVE).count(),
            "description": _("àpats programats aquest mes"),
            "href": reverse("cafeteria:monthly_planning"),
        },
    }
    defaults = ["today_meals", "diet_summary", "daily_reports", "statements", "monthly_plan"]
    if _is_admin(user):
        active_year = _active_year_or_none()
        membership_queryset = AfaMembership.objects.filter(academic_year=active_year) if active_year else AfaMembership.objects.none()
        widget_map.update({
            "contacts": {
                "key": "contacts", "icon": "users", "label": _("Contactes actius"),
                "value": Family.objects.filter(active=True).count(),
                "description": _("famílies registrades"), "href": reverse("cafeteria:contacts_dashboard"),
            },
            "afa_fees": {
                "key": "afa_fees", "icon": "euro", "label": _("Quotes AFA pendents"),
                "value": membership_queryset.filter(status=AfaMembershipStatus.PENDING).count(),
                "description": _("quotes per regularitzar"), "href": reverse("cafeteria:afa_memberships"),
            },
            "invitations": {
                "key": "invitations", "icon": "mail", "label": _("Invitacions pendents"),
                "value": Invitation.objects.filter(accepted_at__isnull=True, expires_at__gt=timezone.now()).count(),
                "description": _("enllaços d'alta vigents"), "href": f"{reverse('cafeteria:portal_administration')}?tab=invitacions",
            },
            "academic": {
                "key": "academic", "icon": "academic", "label": _("Calendari escolar"),
                "value": ServiceDay.objects.filter(academic_year=active_year, is_service_day=True).count() if active_year else 0,
                "description": _("dies lectius amb servei"), "href": reverse("cafeteria:school_calendar"),
            },
        })
        defaults.extend(["contacts", "afa_fees", "invitations", "academic"])
    profile = user.profile
    configured_keys = profile.dashboard_widgets if isinstance(profile.dashboard_widgets, list) else []
    visible_keys = [key for key in configured_keys if key in widget_map] or defaults
    return [widget_map[key] for key in visible_keys], [widget_map[key] for key in widget_map]


@require_GET
def healthcheck(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM django_migrations LIMIT 1")
    except DatabaseError:
        return HttpResponse("unavailable", status=503, content_type="text/plain")
    return HttpResponse("ok", content_type="text/plain")


@login_required
def dashboard(request):
    today = timezone.localdate()
    if not _is_staff(request.user):
        if explicit_role(request.user, Role.KITCHEN):
            return redirect("cafeteria:kitchen_report")
    if _is_staff(request.user):
        widgets, available_widgets = _staff_dashboard_widgets(request.user)
        context = {
            "is_staff": True,
            "today": today,
            "widgets": widgets,
            "available_widgets": available_widgets,
            "selected_widget_keys": [widget["key"] for widget in widgets],
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

    pending_onboarding = _pending_family_onboarding(request.user)
    if pending_onboarding:
        return redirect("cafeteria:family_onboarding", family_id=pending_onboarding.family_id)
    families = Family.objects.filter(memberships__user=request.user, active=True).prefetch_related("students__default_diet")
    upcoming = MealBooking.objects.filter(
        student__family__in=families,
        date__gte=today,
        status=BookingStatus.ACTIVE,
    ).select_related("student", "diet").order_by("date")[:8]
    return render(request, "cafeteria/dashboard_tutor.html", {"families": families, "upcoming": upcoming, "today": today})


@staff_required
@require_POST
def dashboard_preferences(request):
    _widgets, available_widgets = _staff_dashboard_widgets(request.user)
    allowed = {widget["key"] for widget in available_widgets}
    selected = []
    for key in request.POST.getlist("widgets"):
        if key in allowed and key not in selected:
            selected.append(key)
    if not selected:
        messages.error(request, _("Selecciona com a mínim un giny per a l'inici."))
    else:
        request.user.profile.dashboard_widgets = selected
        request.user.profile.save(update_fields=["dashboard_widgets"])
        messages.success(request, _("S'ha personalitzat l'inici."))
    return redirect("cafeteria:dashboard")


@login_required
@require_POST
def navigation_preferences(request):
    section = request.POST.get("section", "")
    collapsed = request.POST.get("collapsed") == "1"
    allowed_sections = {"menjador", "economia", "contactes", "calendari", "portal", "familia", "mi_menjador"}
    if section not in allowed_sections:
        return HttpResponseForbidden(_("Aquesta secció no és vàlida."))
    state = dict(request.user.profile.navigation_state or {})
    state[section] = {"collapsed": collapsed}
    request.user.profile.navigation_state = state
    request.user.profile.save(update_fields=["navigation_state"])
    return HttpResponse(status=204)


@login_required
def app_preferences(request):
    """Keep old account-preference links working after moving the control."""
    return redirect("cafeteria:dashboard")


def _month_starts_for_academic_year(academic_year):
    current = academic_year.starts_on.replace(day=1)
    final_month = academic_year.ends_on.replace(day=1)
    months = []
    while current <= final_month:
        months.append(current)
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def _dates_for_periods(periods, academic_year):
    date_map = {}
    for period in periods:
        current_day = max(period.starts_on, academic_year.starts_on)
        final_day = min(period.ends_on, academic_year.ends_on)
        while current_day <= final_day:
            date_map.setdefault(current_day, []).append(period)
            current_day += timedelta(days=1)
    return date_map


def _build_year_calendar(academic_year, course_group_ids=None):
    """Build a compact, read-only calendar that can be shared by staff and families."""
    if not academic_year:
        return []
    service_day_map = {
        item.date: item for item in ServiceDay.objects.filter(academic_year=academic_year)
    }
    service_dates = {item.date for item in service_day_map.values() if item.is_service_day}
    holidays = list(AcademicHoliday.objects.filter(academic_year=academic_year))
    intensive_periods = list(AcademicIntensivePeriod.objects.filter(academic_year=academic_year))
    notices = list(AcademicNotice.objects.filter(academic_year=academic_year))
    closures = CourseClosure.objects.filter(course_group__academic_year=academic_year).select_related("course_group")
    if course_group_ids is not None:
        closures = closures.filter(course_group_id__in=course_group_ids)
    holiday_dates = _dates_for_periods(holidays, academic_year)
    intensive_dates = _dates_for_periods(intensive_periods, academic_year)
    notice_dates = _dates_for_periods(notices, academic_year)
    closure_dates = {}
    for closure in closures:
        closure_dates.setdefault(closure.date, []).append(closure)

    months = []
    for month_start in _month_starts_for_academic_year(academic_year):
        weeks = []
        for week in calendar.Calendar(firstweekday=0).monthdatescalendar(month_start.year, month_start.month):
            cells = []
            for current_day in week:
                in_academic_year = academic_year.starts_on <= current_day <= academic_year.ends_on
                cells.append({
                    "date": current_day,
                    "in_month": current_day.month == month_start.month,
                    "in_academic_year": in_academic_year,
                    "is_service_day": current_day in service_dates and current_day not in holiday_dates,
                    "service_day": service_day_map.get(current_day),
                    "is_weekend": current_day.weekday() >= 5,
                    "holidays": holiday_dates.get(current_day, []),
                    "intensive_periods": intensive_dates.get(current_day, []),
                    "closures": closure_dates.get(current_day, []),
                    "notices": notice_dates.get(current_day, []),
                    "has_alert_notice": any(
                        notice.level == "alert" for notice in notice_dates.get(current_day, [])
                    ),
                })
            weeks.append(cells)
        months.append({"start": month_start, "weeks": weeks})
    return months


@login_required
def family_home(request):
    pending_onboarding = _pending_family_onboarding(request.user)
    if pending_onboarding:
        return redirect("cafeteria:family_onboarding", family_id=pending_onboarding.family_id)
    families = Family.objects.filter(memberships__user=request.user, active=True).prefetch_related("students")
    if not families.exists():
        messages.info(request, _("No tens cap família activa vinculada al teu compte."))
        return redirect("cafeteria:dashboard")
    return render(request, "cafeteria/family_home.html", {"families": families})


@login_required
@require_POST
def family_context_select(request):
    family = get_object_or_404(
        Family, pk=positive_pk(request.POST.get("family_id")), active=True, memberships__user=request.user
    )
    request.session["cafeteria_active_family_id"] = family.id
    return redirect("cafeteria:family_home")


@login_required
def family_onboarding(request, family_id):
    """Shared initial setup when a family needs to create its first student."""
    membership = get_object_or_404(
        FamilyMembership.objects.select_related("family"),
        family_id=family_id,
        user=request.user,
    )
    family = membership.family
    portal = PortalSettings.objects.first()
    if (
        not portal
        or not portal.allow_family_student_creation
        or not membership.onboarding_required
        or membership.onboarding_completed_at
        or family.students.filter(active=True).exists()
    ):
        return redirect("cafeteria:family_home")
    active_year = _active_year_or_none()
    if not active_year or not CourseGroup.objects.filter(academic_year=active_year).exists():
        return render(request, "cafeteria/family_onboarding.html", {
            "family": family,
            "registration_unavailable": True,
        })
    students = list(family.students.filter(active=True).select_related("default_diet", "course_group"))
    family_form = FamilyOnboardingContactForm(request.POST or None, instance=family, prefix="family")
    student_forms = [
        TutorStudentForm(
            request.POST or None,
            request.FILES or None,
            instance=student,
            prefix=f"student-{student.id}",
            require_profile_completion=True,
            actor=request.user,
        )
        for student in students
    ]
    NewStudentFormSet = formset_factory(
        FamilyStudentCreateForm,
        extra=1,
        can_delete=True,
        min_num=1 if not students else 0,
        validate_min=not students,
    )
    new_student_forms = NewStudentFormSet(
        request.POST or None,
        request.FILES or None,
        prefix="new-students",
        form_kwargs={"academic_year": active_year, "require_profile_completion": True, "actor": request.user, "family": family},
    )
    if (
        request.method == "POST"
        and family_form.is_valid()
        and all(form.is_valid() for form in student_forms)
        and new_student_forms.is_valid()
    ):
        with transaction.atomic():
            family_form.save()
            for form in student_forms:
                saved_student = form.save()
                reprice_open_bookings(student=saved_student)
                log_event(request.user, "student.initial_profile_completed", saved_student)
            for form in new_student_forms:
                if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                    continue
                saved_student = form.save(commit=False)
                saved_student.family = family
                saved_student.save()
                log_event(request.user, "student.created_by_family", saved_student)
            completed_at = timezone.now()
            FamilyMembership.objects.filter(family=family).update(
                onboarding_required=False,
                onboarding_completed_at=completed_at,
            )
            log_event(request.user, "family.initial_profile_completed", family)
        messages.success(request, _("La configuració inicial de la família ja està completada."))
        return redirect("cafeteria:family_home")
    return render(request, "cafeteria/family_onboarding.html", {
        "family": family,
        "family_form": family_form,
        "student_form_rows": list(zip(students, student_forms)),
        "new_student_forms": new_student_forms,
        "active_year": active_year,
    })


@login_required
def family_profile(request, family_id):
    family = _family_for_user_or_404(request.user, family_id)
    pending_onboarding = _pending_family_onboarding(request.user, family.id)
    if pending_onboarding:
        return redirect("cafeteria:family_onboarding", family_id=family.id)
    form = FamilyContactForm(request.POST or None, instance=family)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        log_event(request.user, "family.contact_updated_by_tutor", saved)
        messages.success(request, _("S'han actualitzat les dades de contacte de la família."))
        return redirect("cafeteria:family_profile", family_id=saved.id)
    return render(request, "cafeteria/family_profile.html", {
        "family": family,
        "form": form,
        "students": family.students.filter(active=True).select_related("course_group", "default_diet"),
        "profile_needs_completion": _family_profile_needs_completion(family),
        "can_add_students": bool(PortalSettings.objects.filter(allow_family_student_creation=True).exists()),
    })


@login_required
def family_student_create(request, family_id):
    """Let a family add a student only while self-service is enabled."""
    family = _family_for_user_or_404(request.user, family_id)
    if not PortalSettings.objects.filter(allow_family_student_creation=True).exists():
        raise Http404(_("L'autogestió d'alumnat no està disponible."))
    active_year = _active_year_or_none()
    if not active_year or not CourseGroup.objects.filter(academic_year=active_year).exists():
        messages.error(request, _("L'administració ha de configurar el curs acadèmic i els grups abans de donar d'alta alumnat."))
        return redirect("cafeteria:family_profile", family_id=family.id)
    form = FamilyStudentCreateForm(
        request.POST or None,
        request.FILES or None,
        academic_year=active_year,
        require_profile_completion=True,
        actor=request.user, family=family,
    )
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        saved.family = family
        saved.save()
        log_event(request.user, "student.created_by_family", saved)
        messages.success(request, _("S'ha afegit la fitxa de l'infant."))
        return redirect("cafeteria:family_profile", family_id=family.id)
    return render(request, "cafeteria/entity_form.html", {
        "form": form,
        "title": _("Afegeix alumnat a %(family)s") % {"family": family.name},
        "back_url": reverse("cafeteria:family_profile", args=[family.id]),
        "help_text": _("Selecciona el grup actual i completa la fitxa de menjador. L'ajut de menjador el gestiona l'administració."),
        "student_form": True,
    })


@login_required
def family_school_calendar(request, family_id):
    family = _family_for_user_or_404(request.user, family_id)
    pending_onboarding = _pending_family_onboarding(request.user, family.id)
    if pending_onboarding:
        return redirect("cafeteria:family_onboarding", family_id=family.id)
    family_years = AcademicYear.objects.filter(
        Q(is_active=True) | Q(course_groups__students__family=family)
    ).distinct()
    selected_id = request.GET.get("year")
    academic_year = family_years.filter(pk=positive_pk(selected_id)).first() if selected_id else None
    if academic_year is None:
        academic_year = family_years.filter(is_active=True).first() or family_years.first()
    course_groups = CourseGroup.objects.filter(
        academic_year=academic_year, students__family=family, students__active=True
    ).distinct() if academic_year else CourseGroup.objects.none()
    allowed_group_ids = set(course_groups.values_list("id", flat=True))
    requested_group_ids = {
        int(value) for value in request.GET.getlist("group") if value.isdigit() and int(value) in allowed_group_ids
    }
    selected_group_ids = requested_group_ids or allowed_group_ids
    excursions = CourseClosure.objects.filter(
        course_group__academic_year=academic_year,
        course_group_id__in=selected_group_ids,
    ).select_related("course_group").order_by("date", "course_group__sort_order", "course_group__name") if academic_year else CourseClosure.objects.none()
    return render(request, "cafeteria/family_school_calendar.html", {
        "family": family,
        "academic_year": academic_year,
        "years": family_years,
        "course_groups": course_groups,
        "selected_group_ids": selected_group_ids,
        "year_calendar": _build_year_calendar(academic_year, selected_group_ids),
        "excursions": excursions,
        "intensive_periods": AcademicIntensivePeriod.objects.filter(academic_year=academic_year) if academic_year else [],
        "notices": AcademicNotice.objects.filter(academic_year=academic_year) if academic_year else [],
    })


@login_required
def family_calendar(request, family_id):
    family = _family_for_user_or_404(request.user, family_id)
    pending_onboarding = _pending_family_onboarding(request.user, family.id)
    if pending_onboarding:
        return redirect("cafeteria:family_onboarding", family_id=family.id)
    try:
        month_start = datetime.strptime(request.GET.get("month", ""), "%Y-%m").date().replace(day=1)
    except ValueError:
        month_start = timezone.localdate().replace(day=1)
    if not 2 <= month_start.year <= 9998:
        month_start = timezone.localdate().replace(day=1)
    month_end = month_start.replace(day=calendar.monthrange(month_start.year, month_start.month)[1])

    students = list(family.students.filter(active=True).select_related("default_diet", "course_group__academic_year"))
    existing = MealBooking.objects.filter(student__in=students, date__range=(month_start, month_end)).select_related("diet")
    booking_map = {(booking.student_id, booking.date): booking for booking in existing}
    closures = {
        (closure.course_group_id, closure.date): closure
        for closure in CourseClosure.objects.filter(date__range=(month_start, month_end))
    }
    month_days = [month_start + timedelta(days=offset) for offset in range(month_end.day)]
    available_dates, today, today_locked = service_calendar(month_start, month_end)
    staff = _is_staff(request.user)
    closed_month = family.statements.filter(year=month_start.year, month=month_start.month).exclude(status=StatementStatus.PREPARED).exists()
    booking_rows = []
    for student in students:
        days = []
        for service_date in month_days:
            booking = booking_map.get((student.id, service_date))
            days.append({
                "date": service_date, "available": service_date in available_dates, "booking": booking,
                "locked": closed_month or (not staff and (service_date < today or (service_date == today and today_locked))),
                "excursion": closures.get((student.course_group_id, service_date)),
            })
        booking_rows.append({"student": student, "days": days})

    profile, _created = UserProfile.objects.get_or_create(user=request.user)
    booking_view = profile.family_booking_view
    if booking_view not in FamilyBookingView.values:
        booking_view = FamilyBookingView.TABS
    booking_view_form = FamilyBookingPreferenceForm(
        request.POST or None,
        initial={"family_booking_view": booking_view},
    )
    if request.method == "POST" and booking_view_form.is_valid():
        profile.family_booking_view = booking_view_form.cleaned_data["family_booking_view"]
        profile.save(update_fields=["family_booking_view"])
        return redirect(
            f"{reverse('cafeteria:family_calendar', args=[family.id])}?month={month_start:%Y-%m}"
        )
    matrix_days = [
        {
            "date": service_date,
            "cells": [
                {"student": row["student"], "cell": row["days"][index]}
                for row in booking_rows
            ],
        }
        for index, service_date in enumerate(month_days)
    ]

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
        "booking_rows": booking_rows,
        "matrix_days": matrix_days,
        "booking_view": booking_view,
        "booking_view_form": booking_view_form,
        "month_days": month_days,
        "leading_days": range(month_start.weekday()),
        "diets": Diet.objects.filter(active=True),
        "month_start": month_start,
        "previous_month": previous_month,
        "next_month": next_month,
        "has_siblings": len(students) > 1,
        "today_change_notice": today_change_notice if not _is_staff(request.user) else None,
    })


def _booking_cell_payload(student, service_date):
    booking = MealBooking.objects.filter(
        student=student, date=service_date, status=BookingStatus.ACTIVE,
    ).select_related("diet").first()
    if not booking:
        return {"state": "empty", "reserved": False, "diet_id": None, "diet_name": ""}
    return {
        "state": "reserved",
        "reserved": True,
        "diet_id": booking.diet_id,
        "diet_name": booking.diet_name or (booking.diet.name if booking.diet else ""),
    }


def _booking_json_error(result):
    messages_by_result = {
        "locked": _("El termini de canvis per a aquest dia ha acabat."),
        "unavailable": _("Aquest dia no té servei de menjador."),
        "reason_required": _("Cal indicar el motiu del canvi fora de termini."),
        "profile_required": _("Completa primer la configuració inicial de la família."),
    }
    return JsonResponse({"ok": False, "message": messages_by_result.get(result, _("No s'ha pogut actualitzar la reserva."))}, status=409)


@require_POST
@login_required
@atomic_write
def family_booking_update(request, family_id):
    """Persist one family booking immediately from the monthly calendar."""
    family = _family_for_user_or_404(request.user, family_id)
    student = get_object_or_404(Student, pk=positive_pk(request.POST.get("student_id")), family=family, active=True)
    try:
        service_date = date.fromisoformat(request.POST.get("service_date", ""))
    except ValueError:
        return JsonResponse({"ok": False, "message": _("La data no és vàlida.")}, status=400)
    operation = request.POST.get("operation")
    active_booking = MealBooking.objects.filter(
        student=student, date=service_date, status=BookingStatus.ACTIVE,
    ).first()

    if operation == "reserve":
        if active_booking:
            return JsonResponse({"ok": True, "booking": _booking_cell_payload(student, service_date)})
        action, diet = "add", None
    elif operation == "cancel":
        if not active_booking:
            return JsonResponse({"ok": True, "booking": _booking_cell_payload(student, service_date)})
        action, diet = "cancel", None
    elif operation == "diet":
        diet = Diet.objects.filter(pk=positive_pk(request.POST.get("diet_id")), active=True).first()
        if not active_booking or not diet:
            return JsonResponse({"ok": False, "message": _("Selecciona una dieta disponible per a una reserva activa.")}, status=400)
        action = "add"
    else:
        return JsonResponse({"ok": False, "message": _("L'acció de reserva no és vàlida.")}, status=400)

    changed, result = _update_student_booking(
        actor=request.user, student=student, service_date=service_date,
        action=action, diet=diet,
    )
    if result not in {"updated", "unchanged"}:
        return _booking_json_error(result)
    return JsonResponse({
        "ok": True,
        "changed": changed,
        "booking": _booking_cell_payload(student, service_date),
    })


@require_POST
@login_required
@atomic_write
def family_booking_apply(request, family_id):
    """Toggle an editable service day for every active student in one family."""
    family = _family_for_user_or_404(request.user, family_id)
    try:
        service_date = date.fromisoformat(request.POST.get("service_date", ""))
    except ValueError:
        return JsonResponse({"ok": False, "message": _("La data no és vàlida.")}, status=400)
    students = list(family.students.filter(active=True).select_related("default_diet", "course_group"))
    editable = []
    skipped = 0
    for student in students:
        locked = is_tutor_locked(service_date)
        if not is_service_day(service_date, student) or (locked and not _is_staff(request.user)):
            skipped += 1
            continue
        editable.append(student)
    active_ids = set(MealBooking.objects.filter(
        student__in=editable, date=service_date, status=BookingStatus.ACTIVE,
    ).values_list("student_id", flat=True))
    action = "add" if any(student.id not in active_ids for student in editable) else "cancel"
    updated = 0
    for student in editable:
        changed, result = _update_student_booking(
            actor=request.user, student=student, service_date=service_date, action=action, diet=None,
        )
        if changed:
            updated += 1
        elif result not in {"unchanged"}:
            skipped += 1
    return JsonResponse({
        "ok": True, "action": action, "updated": updated, "skipped": skipped,
        "bookings": [{"student_id": student.id, "booking": _booking_cell_payload(student, service_date)} for student in students],
    })


@require_POST
@login_required
@atomic_write
def bulk_booking(request, family_id):
    family = _family_for_user_or_404(request.user, family_id)
    student = get_object_or_404(Student, pk=positive_pk(request.POST.get("student_id")), family=family, active=True)
    selected_dates = []
    for raw_date in request.POST.getlist("dates"):
        try:
            selected_dates.append(date.fromisoformat(raw_date))
        except ValueError:
            continue
    action = request.POST.get("action")
    diet = Diet.objects.filter(pk=positive_pk(request.POST.get("diet_id")), active=True).first()
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
@atomic_write
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
        diets[student.id] = Diet.objects.filter(pk=positive_pk(request.POST.get(f"diet_{student.id}")), active=True).first()
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
        month_start = datetime.strptime(request.GET.get("month", ""), "%Y-%m").date().replace(day=1)
    except ValueError:
        try:
            month_start = date.fromisoformat(request.GET.get("week", "")).replace(day=1)
        except ValueError:
            month_start = timezone.localdate().replace(day=1)
    if not 2 <= month_start.year <= 9998:
        month_start = timezone.localdate().replace(day=1)
    month_end = month_start.replace(day=calendar.monthrange(month_start.year, month_start.month)[1])
    bookings = {booking.date: booking for booking in TeacherMealBooking.objects.filter(
        teacher=profile, date__range=(month_start, month_end)
    ).select_related("diet")}
    available_dates, today, today_locked = service_calendar(month_start, month_end)
    closed_month = TeacherMonthlyStatement.objects.filter(teacher=profile, year=month_start.year, month=month_start.month).exclude(status=StatementStatus.PREPARED).exists()
    month_days = [
        {
            "date": month_start + timedelta(days=offset),
            "available": month_start + timedelta(days=offset) in available_dates,
            "booking": bookings.get(month_start + timedelta(days=offset)),
            "locked": closed_month or not profile.active or month_start + timedelta(days=offset) < today or (month_start + timedelta(days=offset) == today and today_locked),
        }
        for offset in range(month_end.day)
    ]
    return render(request, "cafeteria/teacher_calendar.html", {
        "profile": profile, "month_days": month_days, "leading_days": range(month_start.weekday()),
        "diets": Diet.objects.filter(active=True), "month_start": month_start,
        "previous_month": (month_start - timedelta(days=1)).replace(day=1),
        "next_month": (month_end + timedelta(days=1)).replace(day=1),
    })


def _teacher_booking_cell_payload(profile, service_date):
    booking = TeacherMealBooking.objects.filter(
        teacher=profile, date=service_date, status=BookingStatus.ACTIVE,
    ).select_related("diet").first()
    if not booking:
        return {"state": "empty", "reserved": False, "diet_id": None, "diet_name": ""}
    return {
        "state": "reserved", "reserved": True, "diet_id": booking.diet_id,
        "diet_name": booking.diet_name or (booking.diet.name if booking.diet else ""),
    }


@require_POST
@login_required
@atomic_write
def teacher_booking_update(request):
    if not _is_teacher(request.user):
        return JsonResponse({"ok": False, "message": _("No tens permís per modificar aquestes reserves.")}, status=403)
    profile, _created = TeacherMealProfile.objects.get_or_create(
        user=request.user, defaults={"default_diet": _ordinary_diet()}
    )
    try:
        service_date = date.fromisoformat(request.POST.get("service_date", ""))
    except ValueError:
        return JsonResponse({"ok": False, "message": _("La data no és vàlida.")}, status=400)
    if not profile.active or is_tutor_locked(service_date) or TeacherMonthlyStatement.objects.filter(teacher=profile, year=service_date.year, month=service_date.month).exclude(status=StatementStatus.PREPARED).exists():
        return _booking_json_error("locked")
    if not is_service_day(service_date):
        return _booking_json_error("unavailable")
    operation = request.POST.get("operation")
    booking = TeacherMealBooking.objects.filter(teacher=profile, date=service_date).first()
    active = booking and booking.status == BookingStatus.ACTIVE
    diet = None
    if operation == "reserve":
        if active:
            return JsonResponse({"ok": True, "booking": _teacher_booking_cell_payload(profile, service_date)})
        diet = profile.default_diet or _ordinary_diet()
    elif operation == "cancel":
        if not active:
            return JsonResponse({"ok": True, "booking": _teacher_booking_cell_payload(profile, service_date)})
    elif operation == "diet":
        if not active:
            return JsonResponse({"ok": False, "message": _("Selecciona una dieta disponible per a una reserva activa.")}, status=400)
        diet = Diet.objects.filter(pk=positive_pk(request.POST.get("diet_id")), active=True).first()
        if not diet:
            return JsonResponse({"ok": False, "message": _("Selecciona una dieta disponible.")}, status=400)
    else:
        return JsonResponse({"ok": False, "message": _("L'acció de reserva no és vàlida.")}, status=400)

    if operation == "cancel":
        booking.status = BookingStatus.CANCELLED
        booking.updated_by = request.user
        booking.save(update_fields=["status", "updated_by", "updated_at"])
    elif booking:
        booking.status = BookingStatus.ACTIVE
        booking.diet = diet
        booking.diet_name = diet.name
        booking.updated_by = request.user
        booking.unit_price = PriceRule.amount_for_category(False, profile.meal_plan, service_date)
        booking.save()
    else:
        TeacherMealBooking.objects.create(
            teacher=profile, date=service_date, diet=diet, diet_name=diet.name,
            created_by=request.user, updated_by=request.user,
        )
    DailyReport.objects.filter(date=service_date, sent_at__isnull=False).update(is_outdated=True)
    return JsonResponse({"ok": True, "booking": _teacher_booking_cell_payload(profile, service_date)})


@require_POST
@login_required
@atomic_write
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
    if action not in {"add", "cancel"}:
        return JsonResponse({"ok": False, "message": _("L'acció de reserva no és vàlida.")}, status=400)
    diet = Diet.objects.filter(pk=positive_pk(request.POST.get("diet_id")), active=True).first() or profile.default_diet
    updated = skipped = 0
    for service_date in selected_dates:
        if not profile.active or is_tutor_locked(service_date) or not is_service_day(service_date) or TeacherMonthlyStatement.objects.filter(teacher=profile, year=service_date.year, month=service_date.month).exclude(status=StatementStatus.PREPARED).exists():
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
    target = selected_dates[0].strftime("%Y-%m") if selected_dates else timezone.localdate().strftime("%Y-%m")
    return redirect(f"{reverse('cafeteria:teacher_calendar')}?month={target}")


@login_required
def student_edit(request, student_id):
    if request.user.is_superuser or _is_admin(request.user):
        student = get_object_or_404(Student, pk=student_id)
    else:
        student = get_object_or_404(Student, pk=student_id, family__memberships__user=request.user)
    pending_onboarding = _pending_family_onboarding(request.user, student.family_id)
    if pending_onboarding:
        return redirect("cafeteria:family_onboarding", family_id=student.family_id)
    form = TutorStudentForm(request.POST or None, request.FILES or None, instance=student, actor=request.user)
    if request.method == "POST" and form.is_valid():
        updated = form.save()
        reprice_open_bookings(student=updated)
        log_event(request.user, "student.updated_by_tutor", updated)
        messages.success(request, _("S'ha actualitzat la fitxa de %(name)s.") % {"name": updated.full_name})
        return redirect("cafeteria:family_profile", family_id=updated.family_id)
    return render(request, "cafeteria/student_form.html", {
        "form": form,
        "student": student,
        "allergy_document_url": reverse("cafeteria:allergy_document_download", args=[student.id]) if student.allergy_document and medical_access(request.user, student) else None,
        "can_view_clinical": medical_access(request.user, student),
    })


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
    membership = None
    user.groups.add(Group.objects.get(name=invitation.role))
    if invitation.role == Role.ADMIN:
        user.is_staff = True
        user.save(update_fields=["is_staff"])
    if invitation.role == Role.TUTOR:
        membership, _created = FamilyMembership.objects.get_or_create(
            family=invitation.family,
            user=user,
        )
        portal = PortalSettings.objects.first()
        requires_student_setup = bool(
            portal
            and portal.allow_family_student_creation
            and not invitation.family.students.filter(active=True).exists()
        )
        membership.onboarding_required = requires_student_setup
        if requires_student_setup:
            membership.onboarding_completed_at = None
        elif not membership.onboarding_completed_at:
            membership.onboarding_completed_at = timezone.now()
        membership.save(update_fields=["onboarding_required", "onboarding_completed_at"])
    if invitation.role == Role.TEACHER:
        TeacherMealProfile.objects.get_or_create(user=user, defaults={"default_diet": _ordinary_diet()})
    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=["accepted_at"])
    log_event(user, "invitation.accepted", invitation, {"role": invitation.role})
    return membership


@atomic_write
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
        if request.method != "POST":
            return render(request, "cafeteria/invitation_accept.html", {"invitation": invitation, "existing_account": True})
        membership = _finish_invitation(invitation, existing)
        messages.success(request, _("La invitació s'ha acceptat correctament."))
        if membership and _pending_family_onboarding(existing, membership.family_id):
            return redirect("cafeteria:family_onboarding", family_id=membership.family_id)
        return redirect("cafeteria:dashboard")

    user = User(username=normalize_email(invitation.email), email=normalize_email(invitation.email))
    form = InvitationAcceptanceForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        membership = _finish_invitation(invitation, user)
        login(request, user)
        messages.success(request, _("El teu compte ja està actiu."))
        if membership and _pending_family_onboarding(user, membership.family_id):
            return redirect("cafeteria:family_onboarding", family_id=membership.family_id)
        return redirect("cafeteria:dashboard")
    return render(request, "cafeteria/invitation_accept.html", {"form": form, "invitation": invitation})


@staff_required
def price_rules(request):
    return redirect(f"{reverse('cafeteria:meal_configuration')}?tab=tarifes")


@staff_required
def daily_reports(request):
    try:
        selected_date = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        selected_date = timezone.localdate()
    student_bookings = bookings_for_day(selected_date)
    teacher_bookings = teacher_bookings_for_day(selected_date)
    allergy_alerts = [booking for booking in student_bookings if booking.student.has_operational_allergy_alert]
    for booking in student_bookings:
        if booking.student.meal_safety_hold:
            booking.diet_name = _("ATURA LA PREPARACIÓ")
            booking.student.kitchen_instructions = _("ATURA LA PREPARACIÓ INDIVIDUAL: contacta amb la persona responsable abans de servir. No pressuposis una dieta ordinària.")
    diet_totals = {}
    for booking in list(student_bookings) + list(teacher_bookings):
        name = booking.diet_name or _("Ordinària")
        diet_totals[name] = diet_totals.get(name, 0) + 1
    reports = DailyReport.objects.all()[:50]
    return render(request, "cafeteria/daily_reports.html", {
        "reports": reports, "today": timezone.localdate(), "selected_date": selected_date,
        "student_bookings": student_bookings, "teacher_bookings": teacher_bookings,
        "diet_totals": sorted(diet_totals.items()),
        "daily_total": student_bookings.count() + teacher_bookings.count(),
        "allergy_alerts": allergy_alerts,
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


@login_required
def allergy_review_queue(request):
    if not _is_staff(request.user):
        return HttpResponseForbidden(_("No tens permís per revisar dades de salut."))
    selected_status = request.GET.get("status", AllergyReviewStatus.PENDING)
    valid_statuses = set(AllergyReviewStatus.values)
    if selected_status not in valid_statuses:
        selected_status = AllergyReviewStatus.PENDING
    declarations = Student.objects.filter(
        Q(has_allergy=True, allergy_review_status=selected_status) | Q(safety_hold=True),
    ).select_related("family", "course_group", "allergy_reviewed_by").order_by(
        "allergy_review_status", "family__name", "first_name", "last_name"
    )
    counts = {
        status: Student.objects.filter(has_allergy=True, allergy_review_status=status).count()
        for status in AllergyReviewStatus.values
    }
    return render(request, "cafeteria/allergy_review_queue.html", {
        "declarations": declarations,
        "selected_status": selected_status,
        "counts": counts,
        "review_statuses": AllergyReviewStatus,
    })


@login_required
def allergy_review(request, student_id):
    if not _is_staff(request.user):
        return HttpResponseForbidden(_("No tens permís per revisar dades de salut."))
    student = get_object_or_404(
        Student.objects.select_related("family", "course_group", "allergy_reviewed_by").filter(
            Q(has_allergy=True, allergy_review_status=AllergyReviewStatus.PENDING) | Q(safety_hold=True)),
        pk=student_id,
    )
    form = AllergyReviewForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if student.has_allergy and not has_health_consent(student):
            return HttpResponseForbidden(_("Cal un consentiment de salut vigent abans de validar la declaració."))
        if form.cleaned_data["decision"] == "approve" and settings.PRIVACY_ENFORCED and (
            student.has_allergy is None or not student.default_diet_id or (student.has_allergy and not student.kitchen_instructions.strip())
        ):
            return HttpResponseForbidden(_("Cal una declaració actual i instruccions segures abans de reprendre el servei."))
        if form.cleaned_data["decision"] == "approve":
            student.allergy_review_status = AllergyReviewStatus.APPROVED
            student.safety_hold = False
            student.allergy_rejection_reason = ""
            action = "student_allergy.approved"
            message = _("S'ha validat la declaració d'al·lèrgia.")
        else:
            student.allergy_review_status = AllergyReviewStatus.REJECTED
            student.allergy_rejection_reason = form.cleaned_data["rejection_reason"].strip()
            action = "student_allergy.rejected"
            message = _("S'ha rebutjat la declaració i la família ja pot corregir-la.")
        student.allergy_reviewed_at = timezone.now()
        student.allergy_reviewed_by = request.user
        student.save(update_fields=[
            "allergy_review_status", "allergy_rejection_reason", "allergy_reviewed_at",
            "allergy_reviewed_by", "updated_at", "safety_hold",
        ])
        log_event(request.user, action, student, {
            "status": student.allergy_review_status,
            "title": student.allergy_title,
        })
        messages.success(request, message)
        return redirect("cafeteria:allergy_review_queue")
    return render(request, "cafeteria/allergy_review.html", {
        "student": student,
        "form": form,
        "document_url": reverse("cafeteria:allergy_document_download", args=[student.id]),
    })


@login_required
def allergy_document_download(request, student_id):
    student = get_object_or_404(Student.objects.select_related("family"), pk=student_id)
    if not medical_access(request.user, student):
        return HttpResponseForbidden(_("No tens permís per consultar aquest document mèdic."))
    if not student.allergy_document or not student.allergy_document.storage.exists(student.allergy_document.name):
        raise Http404(_("No s'ha trobat el document mèdic."))
    log_event(request.user, "student_allergy.document_downloaded", student, {
        "document": student.allergy_document_name,
    })
    filename = student.allergy_document_name or Path(student.allergy_document.name).name
    return FileResponse(student.allergy_document.open("rb"), as_attachment=True, filename=filename)


@staff_required
def monthly_planning(request):
    try:
        month_start = datetime.strptime(request.GET.get("month", ""), "%Y-%m").date().replace(day=1)
    except ValueError:
        month_start = timezone.localdate().replace(day=1)
    if not 2 <= month_start.year <= 9998:
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
        day = grouped.setdefault(booking.date, {"students": [], "teachers": [], "diets": {}})
        day["students"].append(booking)
        label = booking.diet_name or _("Ordinària")
        day["diets"][label] = day["diets"].get(label, 0) + 1
    for booking in teacher_bookings:
        day = grouped.setdefault(booking.date, {"students": [], "teachers": [], "diets": {}})
        day["teachers"].append(booking)
        label = booking.diet_name or _("Ordinària")
        day["diets"][label] = day["diets"].get(label, 0) + 1
    available_dates, _today, _today_locked = service_calendar(month_start, month_end)
    days = []
    for offset in range((month_end - month_start).days + 1):
        current = month_start + timedelta(days=offset)
        data = grouped.get(current, {"students": [], "teachers": [], "diets": {}})
        if current in available_dates or data["students"] or data["teachers"]:
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
        if not 1 <= month <= 12 or not 1 <= year <= 9998:
            raise ValueError
    except (KeyError, ValueError):
        messages.error(request, _("Mes no vàlid."))
        return redirect("cafeteria:monthly_statements")
    try:
        count = prepare_statements_for_month(year, month)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return redirect("cafeteria:monthly_statements")
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
@atomic_write
def statement_close(request, statement_id):
    statement = get_object_or_404(MonthlyStatement, pk=statement_id)
    if statement.status == StatementStatus.PREPARED:
        try:
            statement = prepare_monthly_statement(statement.family, statement.year, statement.month)
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
            return redirect("cafeteria:statement_detail", statement_id=statement.id)
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
    writer.writerow([_("Data"), _("Alumne"), _("Dieta"), _("Modalitat"), _("Becat"), _("Import")])
    for line in statement.lines.select_related("student"):
        writer.writerow([csv_cell(value) for value in [
            line.service_date.isoformat(), line.student.full_name, line.diet_name,
            line.get_meal_plan_display(), _("Sí") if line.scholarship else _("No"), line.unit_price,
        ]])
    writer.writerow([])
    writer.writerow([_("Total"), "", "", "", "", statement.total])
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
@atomic_write
def teacher_statement_close(request, statement_id):
    statement = get_object_or_404(TeacherMonthlyStatement, pk=statement_id)
    if statement.status == StatementStatus.PREPARED:
        try:
            statement = prepare_teacher_monthly_statement(statement.teacher, statement.year, statement.month)
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
            return redirect("cafeteria:teacher_statement_detail", statement_id=statement.id)
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
    return redirect(f"{reverse('cafeteria:portal_administration')}?tab=auditoria")


def _language_redirect_path(request, language):
    """Keep an internal portal URL while replacing its explicit language prefix."""
    with translation.override(language):
        fallback = reverse("cafeteria:dashboard")
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return fallback

    parts = urlsplit(next_url)
    segments = parts.path.split("/")
    supported = {code for code, _label in settings.LANGUAGES}
    if len(segments) < 2 or segments[1] not in supported:
        return fallback

    localized_path = "/" + "/".join([language, *segments[2:]])
    return urlunsplit(("", "", localized_path, parts.query, ""))


@require_POST
def set_language(request):
    language = request.POST.get("language")
    supported = {code for code, _label in settings.LANGUAGES}
    if language in supported:
        if request.user.is_authenticated:
            try:
                request.user.profile.language = language
                request.user.profile.save(update_fields=["language"])
            except Exception:
                pass
        response = redirect(_language_redirect_path(request, language))
        response.set_cookie(
            settings.LANGUAGE_COOKIE_NAME,
            language,
            max_age=getattr(settings, "LANGUAGE_COOKIE_AGE", None),
            path=getattr(settings, "LANGUAGE_COOKIE_PATH", "/"),
            domain=getattr(settings, "LANGUAGE_COOKIE_DOMAIN", None),
            secure=getattr(settings, "LANGUAGE_COOKIE_SECURE", False),
            httponly=getattr(settings, "LANGUAGE_COOKIE_HTTPONLY", False),
            samesite=getattr(settings, "LANGUAGE_COOKIE_SAMESITE", "Lax"),
        )
        return response
    return redirect(reverse("cafeteria:dashboard"))


def password_reset_request(request):
    """Password reset that remains safe and usable before SMTP is configured."""
    form = PasswordResetForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        _key, allowed = consume_attempt("password-reset", form.cleaned_data["email"], limit=3)
        if not allowed:
            return redirect("cafeteria:password_reset_done")
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
def portal_administration(request):
    tabs = {"configuracio", "comptes", "invitacions", "copies", "auditoria"}
    tab = request.POST.get("tab") or request.GET.get("tab", "comptes")
    if tab not in tabs:
        tab = "comptes"
    context = {"tab": tab}
    if tab == "configuracio":
        portal = PortalSettings.objects.first() or PortalSettings.objects.get_or_create(pk=1)[0]
        form = PortalFamilyRegistrationSettingsForm(
            request.POST or None,
            instance=portal,
            prefix="family-registration",
        )
        if request.method == "POST" and form.is_valid():
            if (
                form.cleaned_data["allow_family_student_creation"]
                and not CourseGroup.objects.filter(academic_year=_active_year_or_none()).exists()
            ):
                form.add_error(
                    "allow_family_student_creation",
                    _("Per activar aquesta opció, primer crea un curs acadèmic actiu amb almenys un grup."),
                )
            else:
                saved = form.save(commit=False)
                saved.updated_by = request.user
                saved.save()
                log_event(request.user, "portal.family_student_creation_updated", saved, {
                    "enabled": saved.allow_family_student_creation,
                })
                messages.success(request, _("S'ha actualitzat la configuració de les famílies."))
                return redirect(f"{reverse('cafeteria:portal_administration')}?tab=configuracio")
        context["family_registration_form"] = form
    elif tab == "comptes":
        context.update(_accounts_context(request))
        context.update({
            "reset_url": request.session.pop("portal_reset_url", None),
            "reset_account_name": request.session.pop("portal_reset_account", None),
        })
    elif tab == "invitacions":
        context.update({
            "invitations": Invitation.objects.select_related("family", "created_by").order_by("-created_at")[:100],
        })
    elif tab == "auditoria":
        context["events"] = AuditEvent.objects.select_related("actor")[:200]
    else:
        context["restore_confirmation"] = RESTORE_CONFIRMATION
    return render(request, "cafeteria/portal_administration.html", context)


@admin_required
def accounts(request):
    return redirect(f"{reverse('cafeteria:portal_administration')}?tab=comptes")


@admin_required
@require_POST
def account_reset_link(request, user_id):
    account = get_object_or_404(User, pk=user_id, is_active=True)
    reset_url = _password_reset_url(request, account)
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
            messages.warning(request, _("No s'ha pogut enviar el correu. Revisa la configuració SMTP."))
    else:
        messages.info(request, _("Copia l'enllaç i comparteix-lo de manera segura amb la persona registrada.") if settings.DEBUG else _("No s'ha pogut enviar el correu. Revisa la configuració SMTP."))
    log_event(request.user, "account.reset_link_created", account, {"email": account.email})
    if settings.DEBUG:
        request.session["portal_reset_url"] = reset_url
        request.session["portal_reset_account"] = account.get_full_name() or account.email
    else:
        request.session.pop("portal_reset_url", None)
        request.session.pop("portal_reset_account", None)
    return portal_administration(request)


@admin_required
def teacher_profile_edit(request, profile_id):
    profile = get_object_or_404(TeacherMealProfile.objects.select_related("user"), pk=profile_id)
    form = TeacherMealProfileForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        reprice_open_bookings()
        log_event(request.user, "teacher_meal_profile.updated", saved)
        messages.success(request, _("S'ha actualitzat el perfil de menjador."))
        return redirect(f"{reverse('cafeteria:portal_administration')}?tab=comptes")
    return render(request, "cafeteria/entity_form.html", {
        "form": form,
        "title": _("Perfil de menjador de %(name)s") % {"name": profile.full_name},
        "back_url": f"{reverse('cafeteria:portal_administration')}?tab=comptes",
    })


def _portal_backup_response(request, filename):
    if settings.DATA_ENCRYPTION_ENABLED:
        from .backups import build_encrypted_backup
        return FileResponse(build_encrypted_backup(), as_attachment=True, filename=filename, content_type="application/octet-stream")
    if connection.vendor != "sqlite":
        messages.error(request, _("Les còpies des del portal només estan disponibles amb SQLite."))
        return None
    archive = tempfile.TemporaryFile()
    try:
        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as snapshot:
            connection.ensure_connection()
            destination = sqlite3.connect(snapshot.name)
            try:
                connection.connection.backup(destination)
            finally:
                destination.close()
            media_root = Path(settings.MEDIA_ROOT).resolve()
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                bundle.writestr("backup.json", json.dumps({"format": "afa-ordis-backup", "version": 1}))
                bundle.write(snapshot.name, "database.sqlite3")
                if media_root.exists():
                    for media_file in media_root.rglob("*"):
                        if media_file.is_file() and not media_file.is_symlink() and media_file.resolve().is_relative_to(media_root):
                            bundle.write(media_file, f"media/{media_file.relative_to(media_root).as_posix()}")
        archive.seek(0)
        return FileResponse(archive, as_attachment=True, filename=filename, content_type="application/zip")
    except Exception:
        archive.close()
        raise


@admin_required
@require_POST
def portal_backup_download(request):
    suffix = "afaenc" if settings.DATA_ENCRYPTION_ENABLED else "zip"
    filename = f"afa-ordis-{timezone.localtime():%Y%m%d-%H%M%S}.{suffix}"
    response = _portal_backup_response(request, filename)
    if response is not None:
        from .models import BackupCustody
        BackupCustody.objects.create(expires_at=timezone.now() + timedelta(days=settings.BACKUP_RETENTION_DAYS))
        request.session["restore_safety_backup_at"] = timezone.now().timestamp()
        log_event(request.user, "portal.backup_downloaded", None, {"filename": filename})
        return response
    return redirect(f"{reverse('cafeteria:portal_administration')}?tab=copies")


def _current_migrations():
    with connection.cursor() as cursor:
        cursor.execute("SELECT app, name FROM django_migrations")
        return set(cursor.fetchall())


def _validate_restore_database(path, key_id=None):
    source = database_connect(f"file:{path}?mode=ro", uri=True, key_id=key_id)
    try:
        integrity = source.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0].lower() != "ok":
            raise ValueError(_("La còpia no supera la comprovació d'integritat SQLite."))
        try:
            source_migrations = set(source.execute("SELECT app, name FROM django_migrations").fetchall())
        except sqlite3.DatabaseError as error:
            raise ValueError(_("El fitxer no sembla ser una còpia del portal AFA Ordis.")) from error
        if source_migrations != _current_migrations():
            raise ValueError(_("La còpia no és compatible amb la versió actual del portal."))
        tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        required_tables = {"auth_user", "cafeteria_academicyear", "cafeteria_userprofile"}
        if not required_tables.issubset(tables):
            raise ValueError(_("El fitxer no conté l'estructura necessària del portal."))
        for table in connection.introspection.table_names():
            quoted_table = connection.ops.quote_name(table)
            with connection.cursor() as cursor:
                cursor.execute(f"PRAGMA table_info({quoted_table})")
                current_columns = cursor.fetchall()
            if source.execute(f"PRAGMA table_info({quoted_table})").fetchall() != current_columns:
                raise ValueError(_("La còpia no és compatible amb la versió actual del portal."))
        if source.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError(_("La còpia no supera la comprovació d'integritat SQLite."))
    finally:
        source.close()


def _restore_sqlite_database(path, key_id=None):
    database_name = settings.DATABASES["default"]["NAME"]
    if database_name == ":memory:" or "mode=memory" in str(database_name):
        raise ValueError(_("No es pot restaurar una base de dades en memòria."))
    connection.close()
    source = database_connect(f"file:{path}?mode=ro", uri=True, key_id=key_id)
    destination = database_connect(database_name)
    try:
        from .crypto import active_key
        if settings.DATA_ENCRYPTION_ENABLED and key_id and key_id != active_key("database")[0]:
            from .database import export_to_active_key
            with tempfile.TemporaryDirectory(dir=settings.PRIVATE_TEMP_DIR) as work:
                converted_path = Path(work) / "converted.sqlite3"
                export_to_active_key(source, converted_path)
                converted = database_connect(converted_path)
                try:
                    converted.backup(destination)
                finally:
                    converted.close()
        else:
            source.backup(destination)
    finally:
        destination.close()
        source.close()
    connection.close()


def _extract_portal_backup(path):
    staging = Path(tempfile.mkdtemp(prefix="afa-ordis-restore-", dir=settings.PRIVATE_TEMP_DIR))
    try:
        with zipfile.ZipFile(path) as archive:
            items = archive.infolist()
            names = set(archive.namelist())
            if len(items) > 5000 or len(names) != len(items) or sum(item.file_size for item in items) > MAX_BACKUP_UPLOAD_BYTES * 3:
                raise ValueError(_("La còpia ZIP és massa gran o conté massa fitxers."))
            if "backup.json" not in names or "database.sqlite3" not in names:
                raise ValueError(_("El fitxer ZIP no és una còpia completa del portal."))
            try:
                if archive.getinfo("backup.json").file_size > 4096:
                    raise ValueError(_("La còpia ZIP no conté un manifest vàlid."))
                manifest = json.loads(archive.read("backup.json"))
            except (json.JSONDecodeError, KeyError) as error:
                raise ValueError(_("La còpia ZIP no conté un manifest vàlid.")) from error
            if not isinstance(manifest, dict) or manifest.get("format") != "afa-ordis-backup" or manifest.get("version") != 1:
                raise ValueError(_("La còpia ZIP no és compatible amb aquesta versió del portal."))
            for item in archive.infolist():
                member = Path(item.filename)
                if member.is_absolute() or ".." in member.parts or item.is_dir():
                    if item.is_dir():
                        continue
                    raise ValueError(_("La còpia ZIP conté una ruta no segura."))
                if item.filename not in {"backup.json", "database.sqlite3"} and not item.filename.startswith("media/"):
                    raise ValueError(_("La còpia ZIP conté fitxers no reconeguts."))
                destination = staging / member
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
                destination.chmod(0o600)
        return staging, staging / "database.sqlite3"
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _restore_portal_state(database_path, staging, key_id=None):
    """Restore under the exclusive portal lock, rolling back both stores on failure."""
    destination = Path(settings.MEDIA_ROOT)
    if destination.is_symlink() or destination.resolve() in {Path("/"), settings.BASE_DIR.resolve()}:
        raise ValueError(_("La còpia ZIP conté una ruta no segura."))
    destination.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="afa-restore-", dir=destination.parent))
    cleanup_safe = True
    try:
        snapshot_path = work / "before.sqlite3"
        snapshot = database_connect(snapshot_path)
        try:
            connection.ensure_connection()
            connection.connection.backup(snapshot)
        finally:
            snapshot.close()
        if staging:
            if (staging / "media").exists():
                shutil.copytree(staging / "media", work / "new-media")
            else:
                (work / "new-media").mkdir()
        media_swapped = False
        old_media_moved = False
        try:
            if staging:
                if destination.exists():
                    destination.rename(work / "old-media")
                    old_media_moved = True
                (work / "new-media").rename(destination)
                media_swapped = True
            if key_id:
                _restore_sqlite_database(database_path, key_id=key_id)
            else:
                _restore_sqlite_database(database_path)
        except Exception:
            try:
                if media_swapped:
                    destination.rename(work / "failed-media")
                if old_media_moved:
                    (work / "old-media").rename(destination)
                _restore_sqlite_database(snapshot_path)
            except Exception:
                cleanup_safe = False
                logger.critical("Restore rollback failed; recovery files preserved in %s", work, exc_info=True)
                raise
            raise
    finally:
        if cleanup_safe:
            shutil.rmtree(work)


@admin_required
@require_POST
def portal_restore(request):
    safety_backup_at = request.session.get("restore_safety_backup_at")
    safety_is_current = isinstance(safety_backup_at, (int, float)) and (
        timezone.now().timestamp() - safety_backup_at <= RESTORE_SAFETY_WINDOW_SECONDS
    )
    upload = request.FILES.get("backup_file")
    if not safety_is_current:
        messages.error(request, _("Abans de restaurar, descarrega una còpia de seguretat actual des d'aquesta mateixa pantalla."))
        return redirect(f"{reverse('cafeteria:portal_administration')}?tab=copies")
    if not upload or upload.size > MAX_BACKUP_UPLOAD_BYTES:
        messages.error(request, _("Puja una còpia ZIP o SQLite vàlida de menys de 100 MB."))
        return redirect(f"{reverse('cafeteria:portal_administration')}?tab=copies")
    if request.POST.get("confirmation") != RESTORE_CONFIRMATION or not request.user.check_password(request.POST.get("password", "")):
        messages.error(request, _("Cal indicar la contrasenya actual i escriure RESTAURA per confirmar l'operació."))
        return redirect(f"{reverse('cafeteria:portal_administration')}?tab=copies")
    suffix = Path(upload.name).suffix or ".sqlite3"
    temporary_path = None
    staging_path = None
    if not DATABASE_RESTORE_LOCK.acquire(blocking=False):
        messages.error(request, _("Ja hi ha una restauració en curs. Torna-ho a provar en uns instants."))
        return redirect(f"{reverse('cafeteria:portal_administration')}?tab=copies")
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temporary_file:
            temporary_path = temporary_file.name
            for chunk in upload.chunks():
                temporary_file.write(chunk)
        database_path = temporary_path
        key_id = None
        ledger = None
        if settings.DATA_ENCRYPTION_ENABLED:
            from .backups import extract_encrypted_backup
            from .privacy import load_restriction_ledger, read_external_ledger, merge_ledgers
            ledger_upload = request.FILES.get("restriction_ledger")
            if not ledger_upload or request.POST.get("latest_ledger_confirmed") != "on":
                raise ValueError(_("Adjunta el registre de restriccions més recent i confirma que és l'última versió custodiada."))
            ledger = merge_ledgers(load_restriction_ledger(), read_external_ledger(ledger_upload))
            with open(temporary_path, "rb") as encrypted:
                staging_path, database_path, manifest = extract_encrypted_backup(encrypted)
            key_id = manifest["database_key"]
            ledger = merge_ledgers(ledger, json.loads((staging_path / "restrictions.json").read_text()))
        elif zipfile.is_zipfile(temporary_path):
            staging_path, database_path = _extract_portal_backup(temporary_path)
        _validate_restore_database(database_path, key_id=key_id)
        if ledger is not None:
            from .privacy import mark_restore_pending, save_restriction_ledger
            mark_restore_pending()
            save_restriction_ledger(ledger)
        _restore_portal_state(database_path, staging_path, key_id=key_id)
        if ledger is not None:
            from .privacy import finish_restore
            finish_restore()
        Session.objects.all().delete()
        log_event(None, "portal.database_restored", None, {"restored_by": request.user.email})
        request.session.pop("restore_safety_backup_at", None)
        logout(request)
        messages.success(request, _("S'ha restaurat la base de dades. Per seguretat, cal tornar a iniciar sessió."))
        return redirect("cafeteria:login")
    except (OSError, sqlite3.DatabaseError, ValueError, zipfile.BadZipFile, RuntimeError) as error:
        messages.error(request, str(error) or _("No s'ha pogut restaurar la còpia."))
        return redirect(f"{reverse('cafeteria:portal_administration')}?tab=copies")
    finally:
        DATABASE_RESTORE_LOCK.release()
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
        if staging_path:
            shutil.rmtree(staging_path, ignore_errors=True)


RECEIPT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic"}
MAX_RECEIPT_FILE_BYTES = 10 * 1024 * 1024


def _economic_settings():
    return EconomicSettings.objects.get_or_create(pk=1)[0]


def _can_submit_expenses(user):
    if not user.is_authenticated:
        return False
    if _is_admin(user):
        return True
    preferences = _economic_settings()
    profile, _created = UserProfile.objects.get_or_create(user=user)
    return preferences.allow_all_users_expense_submissions or profile.can_submit_expenses


def _validate_receipts(files):
    errors = []
    for uploaded in files:
        extension = Path(uploaded.name).suffix.lower()
        if extension not in RECEIPT_EXTENSIONS:
            errors.append(_("El fitxer %(name)s no és un PDF ni una imatge compatible.") % {"name": uploaded.name})
        elif uploaded.size > MAX_RECEIPT_FILE_BYTES:
            errors.append(_("El fitxer %(name)s supera els 10 MB.") % {"name": uploaded.name})
    return errors


def _store_receipts(entry, files, actor):
    for uploaded in files:
        EconomicAttachment.objects.create(
            entry=entry, file=uploaded, original_name=Path(uploaded.name).name[:255], uploaded_by=actor,
        )


def _economic_entry_snapshot(entry):
    return {
        "type": entry.entry_type, "date": entry.date.isoformat(), "concept": entry.concept,
        "category": entry.category_id, "account": entry.account_id, "amount": str(entry.amount),
        "review": entry.review_status, "payment": entry.payment_status,
        "paid_on": entry.paid_on.isoformat() if entry.paid_on else None, "notes": entry.notes,
        "rejected_reason": entry.rejected_reason,
    }


def _economic_queryset(request):
    entries = EconomicEntry.objects.select_related("category", "account", "submitted_by", "reviewed_by").prefetch_related("attachments")
    selected_type = request.GET.get("type", "")
    selected_status = request.GET.get("status", "")
    selected_account = request.GET.get("account", "")
    selected_category = request.GET.get("category", "")
    mode = request.GET.get("period", "academic")
    selected_year = request.GET.get("year", "")
    selected_academic_year = request.GET.get("academic_year", "")
    academic_year = AcademicYear.objects.filter(pk=positive_pk(selected_academic_year)).first() if selected_academic_year else _active_year_or_none()
    calendar_year = None
    if mode == "calendar":
        try:
            calendar_year = int(selected_year)
            if not 1 <= calendar_year <= 9998:
                raise ValueError
        except (TypeError, ValueError):
            calendar_year = timezone.localdate().year
        entries = entries.filter(date__year=calendar_year)
    elif academic_year:
        entries = entries.filter(date__gte=academic_year.starts_on, date__lte=academic_year.ends_on)
    if selected_type in EconomicEntryType.values:
        entries = entries.filter(entry_type=selected_type)
    if selected_status in EconomicReviewStatus.values:
        entries = entries.filter(review_status=selected_status)
    if positive_pk(selected_account):
        entries = entries.filter(account_id=positive_pk(selected_account))
    if positive_pk(selected_category):
        entries = entries.filter(category_id=positive_pk(selected_category))
    return entries, {
        "selected_type": selected_type, "selected_status": selected_status,
        "selected_account": selected_account, "selected_category": selected_category,
        "period_mode": mode, "academic_year": academic_year, "calendar_year": calendar_year,
    }


def _economic_summary(entries):
    paid = entries.filter(review_status=EconomicReviewStatus.APPROVED, payment_status=EconomicPaymentStatus.PAID)
    income = paid.filter(entry_type=EconomicEntryType.INCOME).aggregate(total=Sum("amount"))["total"] or 0
    expense = paid.filter(entry_type=EconomicEntryType.EXPENSE).aggregate(total=Sum("amount"))["total"] or 0
    pending_payment = entries.filter(
        review_status=EconomicReviewStatus.APPROVED,
        entry_type=EconomicEntryType.EXPENSE,
        payment_status=EconomicPaymentStatus.PENDING,
    ).aggregate(total=Sum("amount"))["total"] or 0
    return {"income": income, "expense": expense, "balance": income - expense, "pending_payment": pending_payment}


@admin_required
def economic_dashboard(request):
    entries, filters = _economic_queryset(request)
    summary = _economic_summary(entries)
    account_rows = []
    posted = Q(entries__review_status=EconomicReviewStatus.APPROVED, entries__payment_status=EconomicPaymentStatus.PAID) & (
        Q(entries__paid_on__gte=F("opening_balance_date")) |
        Q(entries__paid_on__isnull=True, entries__date__gte=F("opening_balance_date"))
    )
    accounts = FinancialAccount.objects.filter(active=True).annotate(
        paid_income=Sum("entries__amount", filter=posted & Q(entries__entry_type=EconomicEntryType.INCOME), default=0),
        paid_expense=Sum("entries__amount", filter=posted & Q(entries__entry_type=EconomicEntryType.EXPENSE), default=0),
    )
    for account in accounts:
        account_rows.append({"account": account, "balance": account.opening_balance + account.paid_income - account.paid_expense})
    return render(request, "cafeteria/economic_dashboard.html", {
        "summary": summary, "entries": entries[:8], "account_rows": account_rows,
        "pending_review_count": EconomicEntry.objects.filter(review_status=EconomicReviewStatus.SUBMITTED).count(),
        **filters,
    })


@admin_required
def economic_entries(request):
    entries, filters = _economic_queryset(request)
    return render(request, "cafeteria/economic_entries.html", {
        "entries": entries[:300], "categories": EconomicCategory.objects.filter(active=True),
        "accounts": FinancialAccount.objects.filter(active=True), "years": AcademicYear.objects.all(), **filters,
    })


@admin_required
def economic_export_csv(request):
    entries, _filters = _economic_queryset(request)
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow([
        _("Data"), _("Tipus"), _("Concepte"), _("Categoria"), _("Compte"), _("Import"), _("Revisió"), _("Pagament"), _("Data de pagament"), _("Presentada per"), _("Justificants"),
    ])
    for entry in entries:
        writer.writerow([csv_cell(value) for value in [
            entry.date.isoformat(), entry.get_entry_type_display(), entry.concept, entry.category.name,
            entry.account.name if entry.account else "", f"{entry.amount:.2f}", entry.get_review_status_display(),
            entry.get_payment_status_display(), entry.paid_on.isoformat() if entry.paid_on else "",
            entry.submitted_by.get_full_name() or entry.submitted_by.email if entry.submitted_by else "",
            entry.attachments.count(),
        ]])
    response = HttpResponse("\ufeff" + output.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="gestio-economica-afa.csv"'
    return response


@admin_required
def economic_reports(request):
    entries, filters = _economic_queryset(request)
    paid_entries = entries.filter(
        review_status=EconomicReviewStatus.APPROVED,
        payment_status=EconomicPaymentStatus.PAID,
    )
    category_totals = paid_entries.values("entry_type", "category__name").annotate(total=Sum("amount")).order_by("entry_type", "category__name")
    return render(request, "cafeteria/economic_reports.html", {
        "summary": _economic_summary(entries), "category_totals": category_totals,
        "categories": EconomicCategory.objects.filter(active=True), "accounts": FinancialAccount.objects.filter(active=True),
        "years": AcademicYear.objects.all(), **filters,
    })


@admin_required
@atomic_write
def economic_entry_form(request, entry_id=None):
    entry = get_object_or_404(EconomicEntry.objects.prefetch_related("attachments"), pk=entry_id) if entry_id else None
    if entry and entry.review_status != EconomicReviewStatus.APPROVED:
        messages.error(request, _("Només es poden editar moviments aprovats."))
        return redirect("cafeteria:economic_entries")
    before = _economic_entry_snapshot(entry) if entry else None
    form = EconomicEntryForm(request.POST or None, instance=entry, initial={
        "date": timezone.localdate(), "payment_status": EconomicPaymentStatus.PAID, "paid_on": timezone.localdate(),
    } if entry is None else None)
    uploads = request.FILES.getlist("attachments") + request.FILES.getlist("camera_attachments")
    if request.method == "POST" and form.is_valid():
        errors = _validate_receipts(uploads)
        if not uploads and not (entry and entry.attachments.exists()):
            errors.append(_("Adjunta com a mínim un justificant."))
        if errors:
            for error in errors:
                form.add_error(None, error)
        else:
            saved = form.save(commit=False)
            saved.review_status = EconomicReviewStatus.APPROVED
            saved.rejected_reason = ""
            saved.reviewed_by = request.user
            saved.reviewed_at = timezone.now()
            saved.submitted_by = saved.submitted_by or request.user
            saved.full_clean()
            saved.save()
            _store_receipts(saved, uploads, request.user)
            after = _economic_entry_snapshot(saved)
            log_event(request.user, "economic_entry.created" if entry is None else "economic_entry.updated", saved, {
                "before": before, "after": after, "attachments_added": len(uploads),
            })
            messages.success(request, _("S'ha desat el moviment econòmic."))
            return redirect("cafeteria:economic_entries")
    return render(request, "cafeteria/economic_entry_form.html", {
        "form": form, "entry": entry, "title": _("Nou moviment") if entry is None else _("Edita el moviment"),
        "back_url": reverse("cafeteria:economic_entries"),
    })


@admin_required
@atomic_write
def economic_review(request, entry_id):
    entry = get_object_or_404(EconomicEntry.objects.prefetch_related("attachments"), pk=entry_id, review_status=EconomicReviewStatus.SUBMITTED)
    before = _economic_entry_snapshot(entry)
    form = EconomicEntryForm(request.POST or None, instance=entry)
    uploads = request.FILES.getlist("attachments") + request.FILES.getlist("camera_attachments")
    if request.method == "POST" and form.is_valid():
        action = request.POST.get("action")
        if action not in {"approve", "reject"}:
            return HttpResponse(status=400)
        saved = form.save(commit=False)
        saved.entry_type = EconomicEntryType.EXPENSE
        saved.reviewed_by = request.user
        saved.reviewed_at = timezone.now()
        if action == "reject":
            saved.review_status = EconomicReviewStatus.REJECTED
            saved.rejected_reason = request.POST.get("rejected_reason", "").strip()
        else:
            saved.review_status = EconomicReviewStatus.APPROVED
            saved.rejected_reason = ""
            if not saved.account_id:
                form.add_error("account", _("Selecciona el compte que assumirà la despesa."))
        errors = _validate_receipts(uploads)
        if errors:
            for error in errors:
                form.add_error(None, error)
        if not form.errors:
            try:
                saved.full_clean()
            except ValidationError as error:
                form.add_error(None, " ".join(error.messages))
            else:
                saved.save()
                _store_receipts(saved, uploads, request.user)
                log_event(request.user, "economic_entry.approved" if action != "reject" else "economic_entry.rejected", saved, {
                    "before": before, "after": _economic_entry_snapshot(saved), "attachments_added": len(uploads),
                })
                messages.success(request, _("S'ha actualitzat la proposta."))
                return redirect("cafeteria:economic_entries")
    return render(request, "cafeteria/economic_review.html", {"entry": entry, "form": form})


@admin_required
@require_POST
@atomic_write
def economic_mark_paid(request, entry_id):
    entry = get_object_or_404(EconomicEntry, pk=entry_id, review_status=EconomicReviewStatus.APPROVED)
    if entry.payment_status != EconomicPaymentStatus.PAID:
        entry.payment_status = EconomicPaymentStatus.PAID
        try:
            entry.paid_on = date.fromisoformat(request.POST.get("paid_on", ""))
        except ValueError:
            entry.paid_on = timezone.localdate()
        entry.full_clean()
        entry.save(update_fields=["payment_status", "paid_on", "updated_at"])
        log_event(request.user, "economic_entry.marked_paid", entry, {"paid_on": entry.paid_on.isoformat()})
        messages.success(request, _("S'ha marcat com a pagada."))
    return local_redirect(request, "cafeteria:economic_entries")


@admin_required
def economic_configuration(request):
    settings_object = _economic_settings()
    tab = request.GET.get("tab") or request.POST.get("tab") or "general"
    account = FinancialAccount.objects.filter(pk=positive_pk(request.GET.get("account") or request.POST.get("account_id"))).first()
    category = EconomicCategory.objects.filter(pk=positive_pk(request.GET.get("category") or request.POST.get("category_id"))).first()
    settings_form = EconomicSettingsForm(instance=settings_object)
    account_form = FinancialAccountForm(instance=account)
    category_form = EconomicCategoryForm(instance=category)
    if request.method == "POST":
        intent = request.POST.get("intent")
        if intent == "settings":
            settings_form = EconomicSettingsForm(request.POST, instance=settings_object)
            if settings_form.is_valid():
                saved = settings_form.save(commit=False)
                saved.updated_by = request.user
                saved.save()
                log_event(request.user, "economic_settings.updated", saved)
                messages.success(request, _("S'ha actualitzat la configuració."))
                return redirect(f"{reverse('cafeteria:economic_configuration')}?tab=general")
        elif intent == "account":
            account_form = FinancialAccountForm(request.POST, instance=account)
            if account_form.is_valid():
                saved = account_form.save()
                log_event(request.user, "financial_account.created" if account is None else "financial_account.updated", saved)
                messages.success(request, _("S'ha desat el compte."))
                return redirect(f"{reverse('cafeteria:economic_configuration')}?tab=comptes")
        elif intent == "category":
            category_form = EconomicCategoryForm(request.POST, instance=category)
            if category_form.is_valid():
                saved = category_form.save()
                log_event(request.user, "economic_category.created" if category is None else "economic_category.updated", saved)
                messages.success(request, _("S'ha desat la categoria."))
                return redirect(f"{reverse('cafeteria:economic_configuration')}?tab=categories")
        elif intent == "access":
            selected_ids = {int(value) for value in request.POST.getlist("user_ids") if value.isdigit()}
            for profile in UserProfile.objects.select_related("user").filter(user__is_active=True):
                desired = profile.user_id in selected_ids
                if profile.can_submit_expenses != desired:
                    profile.can_submit_expenses = desired
                    profile.save(update_fields=["can_submit_expenses"])
            log_event(request.user, "economic_submission_access.updated", settings_object, {"user_ids": sorted(selected_ids)})
            messages.success(request, _("S'han actualitzat les autoritzacions."))
            return redirect(f"{reverse('cafeteria:economic_configuration')}?tab=accessos")
    profiles = UserProfile.objects.select_related("user").filter(user__is_active=True).order_by("user__first_name", "user__last_name", "user__email")
    return render(request, "cafeteria/economic_configuration.html", {
        "tab": tab, "settings_form": settings_form, "account_form": account_form, "category_form": category_form,
        "editing_account": account, "editing_category": category, "accounts": FinancialAccount.objects.all(),
        "categories": EconomicCategory.objects.all(), "profiles": profiles,
    })


@login_required
def economic_my_expenses(request):
    if not _can_submit_expenses(request.user):
        return HttpResponseForbidden(_("No tens permís per presentar despeses."))
    entries = EconomicEntry.objects.filter(submitted_by=request.user, entry_type=EconomicEntryType.EXPENSE).prefetch_related("attachments")
    return render(request, "cafeteria/economic_my_expenses.html", {"entries": entries})


@login_required
@atomic_write
def economic_submission_form(request, entry_id=None):
    if not _can_submit_expenses(request.user):
        return HttpResponseForbidden(_("No tens permís per presentar despeses."))
    entry = None
    if entry_id:
        entry = get_object_or_404(
            EconomicEntry.objects.prefetch_related("attachments"), pk=entry_id, submitted_by=request.user,
            review_status=EconomicReviewStatus.SUBMITTED,
        )
    form = EconomicSubmissionForm(request.POST or None, instance=entry, initial={"date": timezone.localdate()} if entry is None else None)
    uploads = request.FILES.getlist("attachments") + request.FILES.getlist("camera_attachments")
    if request.method == "POST" and form.is_valid():
        errors = _validate_receipts(uploads)
        if not uploads and not (entry and entry.attachments.exists()):
            errors.append(_("Adjunta com a mínim un justificant."))
        if errors:
            for error in errors:
                form.add_error(None, error)
        else:
            saved = form.save(commit=False)
            saved.entry_type = EconomicEntryType.EXPENSE
            saved.account = None
            saved.review_status = EconomicReviewStatus.SUBMITTED
            saved.payment_status = EconomicPaymentStatus.PENDING
            saved.paid_on = None
            saved.rejected_reason = ""
            saved.submitted_by = request.user
            saved.reviewed_by = None
            saved.reviewed_at = None
            saved.save()
            _store_receipts(saved, uploads, request.user)
            log_event(request.user, "economic_entry.submitted" if entry is None else "economic_entry.submission_updated", saved, {"attachments_added": len(uploads)})
            messages.success(request, _("S'ha enviat la despesa per revisar."))
            return redirect("cafeteria:economic_my_expenses")
    return render(request, "cafeteria/economic_submission_form.html", {
        "form": form, "entry": entry, "title": _("Presenta una despesa") if entry is None else _("Edita la despesa"),
    })


@login_required
@require_POST
@atomic_write
def economic_submission_withdraw(request, entry_id):
    if not _can_submit_expenses(request.user):
        return HttpResponseForbidden(_("No tens permís per presentar despeses."))
    entry = get_object_or_404(
        EconomicEntry, pk=entry_id, submitted_by=request.user, review_status=EconomicReviewStatus.SUBMITTED,
    )
    entry.review_status = EconomicReviewStatus.WITHDRAWN
    entry.save(update_fields=["review_status", "updated_at"])
    log_event(request.user, "economic_entry.withdrawn", entry)
    messages.success(request, _("S'ha retirat la proposta."))
    return redirect("cafeteria:economic_my_expenses")


@login_required
def economic_attachment_download(request, attachment_id):
    attachment = get_object_or_404(EconomicAttachment.objects.select_related("entry"), pk=attachment_id)
    if not _is_admin(request.user) and attachment.entry.submitted_by_id != request.user.id:
        return HttpResponseForbidden(_("No tens permís per consultar aquest justificant."))
    if not attachment.file or not attachment.file.storage.exists(attachment.file.name):
        raise Http404
    log_event(request.user, "economic_attachment.downloaded", attachment.entry, {"attachment": attachment.original_name})
    return FileResponse(attachment.file.open("rb"), as_attachment=True, filename=attachment.original_name)


@staff_required
def menu_settings(request):
    return redirect(f"{reverse('cafeteria:meal_configuration')}?tab=menu")


def _active_year_or_none():
    return AcademicYear.objects.filter(is_active=True).first() or AcademicYear.objects.first()


@admin_required
def management_dashboard(request):
    return dashboard(request)


@staff_required
def dining_dashboard(request):
    return dashboard(request)


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
    academic_year = AcademicYear.objects.filter(pk=positive_pk(selected_id)).first() if selected_id else _active_year_or_none()
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
    academic_year = AcademicYear.objects.filter(pk=positive_pk(selected_id)).first() if selected_id else _active_year_or_none()
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
    form = StaffStudentForm(
        request.POST or None,
        request.FILES or None,
        instance=student,
        require_profile_completion=student is None,
        actor=request.user,
    )
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        reprice_open_bookings(student=saved)
        log_event(request.user, "student.created" if student is None else "student.updated", saved)
        messages.success(request, _("S'ha desat la fitxa de l'alumne."))
        return redirect("cafeteria:people")
    return render(request, "cafeteria/entity_form.html", {
        "form": form,
        "title": _("Nou alumne") if student is None else _("Edita la fitxa de l'alumne"),
        "back_url": reverse("cafeteria:people"),
        "allergy_document_url": reverse("cafeteria:allergy_document_download", args=[student.id]) if student and student.allergy_document and medical_access(request.user, student) else None,
        "student_form": True,
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
    year = AcademicYear.objects.filter(pk=positive_pk(selected_id)).first() if selected_id else _active_year_or_none()
    return render(request, "cafeteria/school_calendar.html", {
        "year": year,
        "years": AcademicYear.objects.all(),
        "year_calendar": _build_year_calendar(year),
        "all_holidays": AcademicHoliday.objects.filter(academic_year=year),
        "all_intensive_periods": AcademicIntensivePeriod.objects.filter(academic_year=year),
        "all_notices": AcademicNotice.objects.filter(academic_year=year),
        "all_closures": CourseClosure.objects.filter(course_group__academic_year=year).select_related("course_group"),
    })


@admin_required
def course_management(request):
    selected_id = request.GET.get("year")
    year = AcademicYear.objects.filter(pk=positive_pk(selected_id)).first() if selected_id else _active_year_or_none()
    course_groups = CourseGroup.objects.filter(academic_year=year).annotate(
        student_total=Count("students", distinct=True),
        closure_total=Count("closures", distinct=True),
    )
    return render(request, "cafeteria/course_management.html", {
        "year": year,
        "years": AcademicYear.objects.all(),
        "course_groups": course_groups,
    })


@admin_required
@atomic_write
def academic_year_save(request, year_id=None):
    academic_year = get_object_or_404(AcademicYear, pk=year_id) if year_id else None
    form = AcademicYearForm(request.POST or None, instance=academic_year)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            saved = form.save()
            MealSettings.objects.get_or_create(academic_year=saved)
            AfaFeeSettings.objects.get_or_create(academic_year=saved)
            reconciliation = _reconcile_academic_year_dates(saved, request.user)
            log_event(
                request.user,
                "academic_year.created" if academic_year is None else "academic_year.updated",
                saved,
                reconciliation,
            )
        messages.success(request, _("S'ha desat el curs acadèmic."))
        if any(reconciliation.values()):
            messages.info(
                request,
                _("S'han ajustat els elements del calendari i les reserves afectades que quedaven fora del nou període."),
            )
        return redirect(f"{reverse('cafeteria:course_management')}?year={saved.id}")

    if request.method == "POST" and academic_year is None:
        messages.error(request, _("No s'ha pogut desar el curs. Revisa les dates."))
        return redirect("cafeteria:course_management")

    return render(request, "cafeteria/entity_form.html", {
        "form": form,
        "title": _("Nou curs acadèmic") if academic_year is None else _("Edita el curs acadèmic"),
        "back_url": (
            reverse("cafeteria:course_management")
            if academic_year is None
            else f"{reverse('cafeteria:course_management')}?year={academic_year.id}"
        ),
        "help_text": _(
            "Pots corregir el nom i el període del curs. Si l'amplies, genera després els nous dies lectius. "
            "En escurçar-lo es retiren els dies, les excursions i les reserves que queden fora del període."
        ),
    })


def _reconcile_academic_year_dates(academic_year, actor):
    """Keep dated academic data valid after changing an academic-year period."""
    outside_service_days = ServiceDay.objects.filter(academic_year=academic_year).exclude(
        date__range=(academic_year.starts_on, academic_year.ends_on)
    )
    outside_dates = list(outside_service_days.values_list("date", flat=True))
    result = {
        "service_days": len(outside_dates),
        "student_bookings": 0,
        "teacher_bookings": 0,
        "closures": 0,
        "holidays_adjusted": 0,
        "intensive_periods_adjusted": 0,
        "notices_adjusted": 0,
    }
    if outside_dates:
        reason = _("Fora del període lectiu corregit")
        result["student_bookings"] = MealBooking.objects.filter(
            student__course_group__academic_year=academic_year,
            date__in=outside_dates,
            status=BookingStatus.ACTIVE,
        ).update(status=BookingStatus.CANCELLED, updated_by=actor, override_reason=reason)
        result["teacher_bookings"] = TeacherMealBooking.objects.filter(
            date__in=outside_dates,
            status=BookingStatus.ACTIVE,
        ).update(status=BookingStatus.CANCELLED, updated_by=actor, override_reason=reason)
        DailyReport.objects.filter(date__in=outside_dates).update(is_outdated=True)
        outside_service_days.delete()

    closures = CourseClosure.objects.filter(
        course_group__academic_year=academic_year,
    ).exclude(date__range=(academic_year.starts_on, academic_year.ends_on))
    result["closures"] = closures.count()
    closures.delete()

    for model, key in (
        (AcademicHoliday, "holidays_adjusted"),
        (AcademicIntensivePeriod, "intensive_periods_adjusted"),
        (AcademicNotice, "notices_adjusted"),
    ):
        for period in model.objects.filter(academic_year=academic_year):
            starts_on = max(period.starts_on, academic_year.starts_on)
            ends_on = min(period.ends_on, academic_year.ends_on)
            if starts_on > ends_on:
                period.delete()
                result[key] += 1
            elif starts_on != period.starts_on or ends_on != period.ends_on:
                period.starts_on = starts_on
                period.ends_on = ends_on
                period.save(update_fields=["starts_on", "ends_on", "updated_at"])
                result[key] += 1
    return result


@admin_required
@require_POST
def generate_service_days(request, year_id):
    year = get_object_or_404(AcademicYear, pk=year_id)
    created = _weekday_service_days(year)
    log_event(request.user, "service_days.generated", year, {"weekday_rows": created})
    messages.success(request, _("S'han preparat els dies lectius de dilluns a divendres."))
    return redirect(f"{reverse('cafeteria:course_management')}?year={year.id}")


@admin_required
@require_POST
@atomic_write
def service_day_toggle(request, service_date):
    try:
        service_date = date.fromisoformat(service_date)
    except ValueError:
        raise Http404
    day = get_object_or_404(ServiceDay, pk=positive_pk(request.POST.get("service_day")), date=service_date)
    day.is_service_day = request.POST.get("is_service_day") == "1"
    day.note = request.POST.get("note", "").strip()
    day.save(update_fields=["is_service_day", "note"])
    log_event(request.user, "service_day.updated", day, {"open": day.is_service_day})
    return local_redirect(request, "cafeteria:school_calendar")


@admin_required
@require_POST
@atomic_write
def service_day_by_date_toggle(request, year_id, service_date):
    """Change the state of one day directly from the annual calendar."""
    academic_year = get_object_or_404(AcademicYear, pk=year_id)
    try:
        selected_date = date.fromisoformat(service_date)
    except ValueError:
        return HttpResponseForbidden(_("La data no és vàlida."))
    if not academic_year.starts_on <= selected_date <= academic_year.ends_on:
        return HttpResponseForbidden(_("La data no pertany al curs seleccionat."))
    day, _created = ServiceDay.objects.get_or_create(
        academic_year=academic_year,
        date=selected_date,
        defaults={"is_service_day": False},
    )
    day.is_service_day = request.POST.get("is_service_day") == "1"
    day.note = request.POST.get("note", "").strip()
    day.save(update_fields=["is_service_day", "note"])
    log_event(request.user, "service_day.updated", day, {"open": day.is_service_day})
    messages.success(request, _("S'ha actualitzat el dia del calendari."))
    return redirect(f"{reverse('cafeteria:school_calendar')}?year={academic_year.id}")


@admin_required
def course_group_save(request, group_id=None):
    group = get_object_or_404(CourseGroup, pk=group_id) if group_id else None
    selected_year = AcademicYear.objects.filter(pk=positive_pk(request.GET.get("year"))).first()
    form = CourseGroupForm(
        request.POST or None,
        instance=group,
        initial={"academic_year": selected_year} if group is None and selected_year else None,
    )
    if group:
        form.fields["academic_year"].disabled = True
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        log_event(request.user, "course_group.created" if group is None else "course_group.updated", saved)
        messages.success(request, _("S'ha desat el curs o grup."))
        return redirect(f"{reverse('cafeteria:course_management')}?year={saved.academic_year_id}")

    if request.method == "POST" and group is None:
        messages.error(request, _("No s'ha pogut desar el grup."))
        return redirect("cafeteria:course_management")

    return render(request, "cafeteria/entity_form.html", {
        "form": form,
        "title": _("Nou grup") if group is None else _("Edita el grup"),
        "back_url": (
            f"{reverse('cafeteria:course_management')}?year={group.academic_year_id}"
            if group
            else f"{reverse('cafeteria:course_management')}?year={selected_year.id}"
            if selected_year
            else reverse("cafeteria:course_management")
        ),
        "help_text": _(
            "Pots corregir el nom i l'ordre del grup. Per mantenir coherents les excursions, "
            "el grup es manté dins del seu curs acadèmic."
        ),
    })


@admin_required
@require_POST
def course_group_delete(request, group_id):
    group = get_object_or_404(CourseGroup, pk=group_id)
    year_id = group.academic_year_id
    with transaction.atomic():
        student_count = group.students.count()
        closure_dates = list(group.closures.values_list("date", flat=True))
        closure_count = len(closure_dates)
        DailyReport.objects.filter(date__in=closure_dates).update(is_outdated=True)
        group_name = group.name
        log_event(request.user, "course_group.deleted", group, {
            "students_unassigned": student_count,
            "closures_deleted": closure_count,
        })
        group.delete()
    messages.success(
        request,
        _("S'ha eliminat el grup %(group)s. %(students)d alumnat queda sense grup i %(closures)d excursions s'han eliminat.") % {
            "group": group_name,
            "students": student_count,
            "closures": closure_count,
        },
    )
    return redirect(f"{reverse('cafeteria:course_management')}?year={year_id}")


@admin_required
def course_closure_save(request, closure_id=None):
    closure = get_object_or_404(CourseClosure, pk=closure_id) if closure_id else None
    selected_year = AcademicYear.objects.filter(pk=positive_pk(request.GET.get("year"))).first() or _active_year_or_none()
    try:
        selected_date = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        selected_date = None
    form = CourseClosureForm(
        request.POST or None,
        instance=closure,
        initial={"date": selected_date} if closure is None and selected_date else None,
    )
    if closure is None and selected_year:
        form.fields["course_group"].queryset = CourseGroup.objects.filter(academic_year=selected_year)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        log_event(request.user, "course_closure.created" if closure is None else "course_closure.updated", saved)
        messages.success(request, _("S'ha desat l'excursió. Les reserves es mantenen sense canvis."))
        return redirect(f"{reverse('cafeteria:school_calendar')}?year={saved.course_group.academic_year_id}")
    if request.method == "POST":
        messages.error(request, _("No s'ha pogut desar l'excursió."))
    return render(request, "cafeteria/entity_form.html", {
        "form": form,
        "title": _("Nova excursió") if closure is None else _("Edita l'excursió"),
        "back_url": f"{reverse('cafeteria:school_calendar')}?year={selected_year.id}" if selected_year else reverse("cafeteria:school_calendar"),
        "help_text": _("L'excursió és informativa: les famílies encara poden reservar l'àpat amb la dieta que correspongui."),
    })


@admin_required
@require_POST
def course_closure_delete(request, closure_id):
    closure = get_object_or_404(CourseClosure.objects.select_related("course_group"), pk=closure_id)
    year_id = closure.course_group.academic_year_id
    DailyReport.objects.filter(date=closure.date).update(is_outdated=True)
    log_event(request.user, "course_closure.deleted", closure)
    closure.delete()
    messages.success(request, _("S'ha eliminat l'excursió."))
    return redirect(f"{reverse('cafeteria:school_calendar')}?year={year_id}")


@admin_required
def academic_holiday_form(request, holiday_id=None):
    holiday = get_object_or_404(AcademicHoliday, pk=holiday_id) if holiday_id else None
    selected_id = request.GET.get("year")
    selected_year = AcademicYear.objects.filter(pk=positive_pk(selected_id)).first() if selected_id else _active_year_or_none()
    form = AcademicHolidayForm(
        request.POST or None,
        instance=holiday,
        initial={"academic_year": selected_year, "starts_on": selected_year.starts_on if selected_year else None} if holiday is None else None,
    )
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        log_event(request.user, "academic_holiday.created" if holiday is None else "academic_holiday.updated", saved)
        messages.success(request, _("S'ha desat el període festiu."))
        return redirect(f"{reverse('cafeteria:school_calendar')}?year={saved.academic_year_id}")
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
    year_id = holiday.academic_year_id
    log_event(request.user, "academic_holiday.deleted", holiday)
    holiday.delete()
    messages.success(request, _("S'ha eliminat el període festiu. Les reserves anul·lades no es reactiven automàticament."))
    return redirect(f"{reverse('cafeteria:school_calendar')}?year={year_id}")


@admin_required
def intensive_period_form(request, period_id=None):
    period = get_object_or_404(AcademicIntensivePeriod, pk=period_id) if period_id else None
    selected_id = request.GET.get("year")
    selected_year = AcademicYear.objects.filter(pk=positive_pk(selected_id)).first() if selected_id else _active_year_or_none()
    form = AcademicIntensivePeriodForm(
        request.POST or None,
        instance=period,
        initial={
            "academic_year": selected_year,
            "starts_on": selected_year.starts_on if selected_year else None,
        } if period is None else None,
    )
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        log_event(request.user, "academic_intensive_period.created" if period is None else "academic_intensive_period.updated", saved)
        messages.success(request, _("S'ha desat el període de jornada intensiva."))
        return redirect(f"{reverse('cafeteria:school_calendar')}?year={saved.academic_year_id}")
    return render(request, "cafeteria/entity_form.html", {
        "form": form,
        "title": _("Nou període de jornada intensiva") if period is None else _("Edita la jornada intensiva"),
        "back_url": reverse("cafeteria:school_calendar"),
        "help_text": _("Aquest període només s'informa als calendaris i no modifica el servei de menjador."),
    })


@admin_required
@require_POST
def intensive_period_delete(request, period_id):
    period = get_object_or_404(AcademicIntensivePeriod, pk=period_id)
    year_id = period.academic_year_id
    log_event(request.user, "academic_intensive_period.deleted", period)
    period.delete()
    messages.success(request, _("S'ha eliminat el període de jornada intensiva."))
    return redirect(f"{reverse('cafeteria:school_calendar')}?year={year_id}")


@admin_required
def academic_notice_form(request, notice_id=None):
    notice = get_object_or_404(AcademicNotice, pk=notice_id) if notice_id else None
    selected_id = request.GET.get("year")
    selected_year = AcademicYear.objects.filter(pk=positive_pk(selected_id)).first() if selected_id else _active_year_or_none()
    try:
        selected_date = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        selected_date = selected_year.starts_on if selected_year else None
    form = AcademicNoticeForm(
        request.POST or None,
        instance=notice,
        initial={
            "academic_year": selected_year,
            "starts_on": selected_date,
            "ends_on": selected_date,
        } if notice is None else None,
    )
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        log_event(request.user, "academic_notice.created" if notice is None else "academic_notice.updated", saved)
        messages.success(request, _("S'ha desat la incidència del calendari."))
        return redirect(f"{reverse('cafeteria:school_calendar')}?year={saved.academic_year_id}")
    return render(request, "cafeteria/entity_form.html", {
        "form": form,
        "title": _("Nova incidència") if notice is None else _("Edita la incidència"),
        "back_url": reverse("cafeteria:school_calendar"),
        "help_text": _("Les incidències informen totes les famílies i no modifiquen el servei de menjador."),
    })


@admin_required
@require_POST
def academic_notice_delete(request, notice_id):
    notice = get_object_or_404(AcademicNotice, pk=notice_id)
    year_id = notice.academic_year_id
    log_event(request.user, "academic_notice.deleted", notice)
    notice.delete()
    messages.success(request, _("S'ha eliminat la incidència del calendari."))
    return redirect(f"{reverse('cafeteria:school_calendar')}?year={year_id}")


@staff_required
@atomic_write
def meal_configuration(request):
    active_year = _active_year_or_none()
    settings_object = MealSettings.objects.filter(academic_year=active_year).first() if active_year else None
    if active_year and settings_object is None:
        settings_object = MealSettings.objects.create(academic_year=active_year)
    tabs = {"avisos", "dietes", "tarifes", "menu"}
    tab = request.POST.get("tab") or request.GET.get("tab") or "avisos"
    if tab not in tabs:
        tab = "avisos"
    settings_form = MealSettingsForm(request.POST or None, instance=settings_object, prefix="settings") if settings_object else None
    diet_form = DietForm(request.POST or None, prefix="diet")
    recipient_form = DailyReportRecipientForm(request.POST or None, prefix="recipient", settings_object=settings_object) if settings_object else None
    price_form = PriceRuleForm(request.POST or None, prefix="price")
    portal = PortalSettings.objects.first() or PortalSettings.objects.get_or_create(pk=1)[0]
    menu_form = PortalSettingsForm(request.POST or None, instance=portal, prefix="menu")
    if request.method == "POST":
        intent = request.POST.get("intent")
        if intent == "settings" and settings_form and settings_form.is_valid():
            saved = settings_form.save()
            log_event(request.user, "meal_settings.updated", saved)
            messages.success(request, _("S'ha actualitzat la configuració del menjador."))
            return redirect(f"{reverse('cafeteria:meal_configuration')}?tab=avisos")
        if intent == "diet" and diet_form.is_valid():
            saved = diet_form.save()
            log_event(request.user, "diet.created", saved)
            messages.success(request, _("S'ha afegit la dieta."))
            return redirect(f"{reverse('cafeteria:meal_configuration')}?tab=dietes")
        if intent == "recipient" and recipient_form and recipient_form.is_valid():
            saved = recipient_form.save(commit=False)
            saved.settings = settings_object
            saved.save()
            log_event(request.user, "daily_recipient.created", saved)
            messages.success(request, _("S'ha afegit el destinatari."))
            return redirect(f"{reverse('cafeteria:meal_configuration')}?tab=avisos")
        if intent == "price" and price_form.is_valid():
            rule = price_form.save(commit=False)
            rule.created_by = request.user
            rule.save()
            reprice_open_bookings(rule=rule)
            log_event(request.user, "price_rule.created", rule, {"amount": str(rule.amount)})
            messages.success(request, _("S'ha guardat la tarifa amb data d'efecte."))
            return redirect(f"{reverse('cafeteria:meal_configuration')}?tab=tarifes")
        if intent == "menu" and menu_form.is_valid():
            saved_portal = menu_form.save(commit=False)
            saved_portal.updated_by = request.user
            saved_portal.save()
            log_event(request.user, "portal.menu_url_updated", saved_portal)
            messages.success(request, _("S'ha actualitzat l'enllaç al menú de l'escola."))
            return redirect(f"{reverse('cafeteria:meal_configuration')}?tab=menu")
        messages.error(request, _("Revisa les dades del formulari."))
    return render(request, "cafeteria/meal_configuration.html", {
        "active_year": active_year, "tab": tab,
        "settings_form": settings_form, "diet_form": diet_form, "price_form": price_form,
        "menu_form": menu_form, "price_rules": PriceRule.objects.all(),
        "recipient_form": recipient_form,
        "diets": Diet.objects.annotate(
            student_total=Count("students", distinct=True),
            teacher_total=Count("teacher_profiles", distinct=True),
        ),
        "recipients": settings_object.daily_recipients.all() if settings_object else [],
    })


@staff_required
def diet_form(request, diet_id=None):
    diet = get_object_or_404(Diet, pk=diet_id) if diet_id else None
    form = DietForm(request.POST or None, instance=diet)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        log_event(request.user, "diet.created" if diet is None else "diet.updated", saved)
        messages.success(request, _("S'ha desat la dieta."))
        return redirect(f"{reverse('cafeteria:meal_configuration')}?tab=dietes")
    return render(request, "cafeteria/entity_form.html", {
        "form": form,
        "title": _("Nova dieta") if diet is None else _("Edita la dieta"),
        "back_url": f"{reverse('cafeteria:meal_configuration')}?tab=dietes",
        "help_text": _(
            "Les dietes inactives no es poden seleccionar en reserves noves, però es conserven a les fitxes que ja les tenen."
        ),
    })


def _replacement_diet(diet):
    replacement = Diet.objects.filter(active=True).exclude(pk=diet.pk).first()
    if replacement:
        return replacement
    replacement = Diet.objects.exclude(pk=diet.pk).first()
    if replacement:
        replacement.active = True
        replacement.save(update_fields=["active"])
        return replacement
    name = "Dieta estàndard"
    suffix = 2
    while Diet.objects.filter(name=name).exists():
        name = f"Dieta estàndard {suffix}"
        suffix += 1
    return Diet.objects.create(
        name=name,
        description="Dieta creada automàticament per conservar les fitxes existents.",
        active=True,
    )


@staff_required
@require_POST
def diet_delete(request, diet_id):
    diet = get_object_or_404(Diet, pk=diet_id)
    with transaction.atomic():
        student_count = diet.students.count()
        teacher_count = diet.teacher_profiles.count()
        replacement = _replacement_diet(diet) if student_count or teacher_count else None
        if replacement:
            Student.objects.filter(default_diet=diet).update(default_diet=replacement)
            TeacherMealProfile.objects.filter(default_diet=diet).update(default_diet=replacement)
        diet_name = diet.name
        log_event(request.user, "diet.deleted", diet, {
            "students_reassigned": student_count,
            "teachers_reassigned": teacher_count,
            "replacement_diet": replacement.name if replacement else None,
        })
        diet.delete()
    if replacement:
        messages.success(
            request,
            _("S'ha eliminat la dieta %(diet)s. Les fitxes que la tenien s'han actualitzat a %(replacement)s.") % {
                "diet": diet_name,
                "replacement": replacement.name,
            },
        )
    else:
        messages.success(request, _("S'ha eliminat la dieta %(diet)s.") % {"diet": diet_name})
    return redirect(f"{reverse('cafeteria:meal_configuration')}?tab=dietes")


CSV_COLUMNS = (
    "family_name,family_phone,student_first_name,student_last_name,"
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
        if None in source:
            errors.append({"row": number, "message": _("Les columnes no coincideixen amb la plantilla descarregable.")})
            continue
        cleaned = {key: (value or "").strip() for key, value in source.items()}
        if not any(cleaned.values()):
            continue
        row_errors = []
        if settings.PRIVACY_ENFORCED and (cleaned["dietary_notes"] or cleaned["default_diet"]):
            row_errors.append(_("Les dades alimentàries s'han de declarar al portal amb les garanties de salut; no es poden importar per CSV."))
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
        except (ValueError, csv.Error) as error:
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
@atomic_write
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
                key = row["family_name"].casefold()
                family = family_cache.get(key)
                if family is None:
                    family = Family.objects.create(
                        name=row["family_name"], phone=row["family_phone"],
                    )
                    family_cache[key] = family
                    created_families += 1
                Student.objects.create(
                    family=family, course_group_id=row["course_group_id"], first_name=row["student_first_name"],
                    last_name=row["student_last_name"], birth_date=row["birth_date"] or None,
                    contact_email=row["student_email"], contact_phone=row["student_phone"], contact_notes=row["contact_notes"],
                    default_diet_id=None if settings.PRIVACY_ENFORCED else row["diet_id"],
                    dietary_notes="" if settings.PRIVACY_ENFORCED else row["dietary_notes"], is_scholarship=row["scholarship_value"],
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
    writer.writerow(["Família exemple", "600000000", "Laia", "Puig", "2019-03-12", "I4", "", "", "", "Ordinària", "", "No", "Fix"])
    return response
