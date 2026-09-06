import calendar
import io
import json
import secrets
import uuid
from datetime import timedelta
from functools import wraps
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from .auth import consume_attempt
from .crypto import encrypt_stream
from .models import (
    BackupCustody, BlockedData, DataRequest, FamilyMembership, PrivacyNotice,
    RecoveryCode, RetentionRule, Role, Student, log_event, user_has_role,
)
from .privacy import (
    backup_overdue, explicit_role, load_restriction_ledger, medical_access,
    privacy_ready, restrict_student,
)
from .privacy_forms import DataRequestForm, MFABeginForm, MFAForm, NoticeForm, RequestReviewForm, RetentionForm, RoleGrantForm


def privacy_staff(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not explicit_role(request.user, Role.PRIVACY):
            return HttpResponseForbidden(_("Cal autorització expressa de privacitat."))
        return view(request, *args, **kwargs)
    return wrapped


def privacy_notice(request):
    notice = PrivacyNotice.current()
    if notice is None:
        # The public notice is approved policy content.  Its database publication
        # is an audit record, not a prerequisite for families to read the terms.
        from .approved_privacy_policy import (
            CONTROLLER, POLICY_EFFECTIVE_DATE, POLICY_VERSION, TEXT_CA, TEXT_ES,
        )
        notice = SimpleNamespace(
            **CONTROLLER, version=POLICY_VERSION, published_at=POLICY_EFFECTIVE_DATE,
        )
        policy_text = TEXT_ES if translation.get_language() == "es" else TEXT_CA
    else:
        policy_text = notice.text_es if translation.get_language() == "es" else notice.text_ca
    return render(request, "cafeteria/privacy_notice.html", {
        "notice": notice,
        "policy_text": policy_text,
    })


@login_required
def privacy_center(request):
    form = DataRequestForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        _key, allowed = consume_attempt("privacy-request", str(request.user.pk), limit=5)
        if not allowed:
            return HttpResponse(_("Massa sol·licituds. Torna-ho a provar més tard."), status=429)
        item = form.save(commit=False)
        now = timezone.now()
        year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
        item.due_at = now.replace(year=year, month=month, day=min(now.day, calendar.monthrange(year, month)[1]))
        item.requester = request.user
        item.save()
        log_event(request.user, "privacy.request_created", item)
        messages.success(request, _("Sol·licitud registrada. Pots seguir-ne la resposta aquí."))
        return redirect("cafeteria:privacy_center")
    return render(request, "cafeteria/privacy_center.html", {
        "form": form,
        "requests": DataRequest.objects.filter(requester=request.user).order_by("-created_at"),
        "students": Student.objects.filter(family__memberships__user=request.user).distinct(),
    })


@login_required
@require_POST
def withdraw_health_consent(request, student_id):
    student = get_object_or_404(Student, pk=student_id, family__memberships__user=request.user)
    if not request.user.check_password(request.POST.get("password", "")):
        return HttpResponseForbidden(_("Confirma la contrasenya actual."))
    restrict_student(student, actor=request.user)
    messages.success(request, _("Consentiment retirat. Les dades de salut han quedat bloquejades. Contacta amb l'AFA per acordar una prestació segura del servei; no s'ha assignat una dieta ordinària."))
    return redirect("cafeteria:privacy_center")


@login_required
def request_export_download(request, request_id):
    item = get_object_or_404(DataRequest, pk=request_id, requester=request.user, resolved_at__isnull=False)
    if not item.export_file or not item.export_expires_at or item.export_expires_at <= timezone.now():
        raise Http404(_("L'exportació ha caducat o encara no està disponible."))
    log_event(request.user, "privacy.export_downloaded", item)
    return FileResponse(item.export_file.open("rb"), as_attachment=True, filename="dades-personals.json")


@privacy_staff
def privacy_administration(request):
    notice_form = NoticeForm(prefix="notice")
    rule_form = RetentionForm(prefix="retention")
    if request.method == "POST":
        if request.POST.get("action") == "publish":
            notice_form = NoticeForm(request.POST, prefix="notice")
            if notice_form.is_valid():
                if set(RetentionRule.objects.values_list("category", flat=True)) != set(RetentionRule.Category.values):
                    notice_form.add_error(None, _("Defineix i justifica tots els terminis de conservació abans de publicar."))
                else:
                    notice = notice_form.save(commit=False)
                    notice.published_at = timezone.now()
                    notice.approved_by = request.user
                    notice.save()
                    log_event(request.user, "privacy.notice_published", notice, {"version": notice.version})
                    return redirect("cafeteria:privacy_administration")
        elif request.POST.get("action") == "retention":
            category = request.POST.get("retention-category", "")
            rule_form = RetentionForm(request.POST, prefix="retention", instance=RetentionRule.objects.filter(category=category).first())
            if rule_form.is_valid():
                rule = rule_form.save(commit=False)
                rule.approved_by = request.user
                rule.approved_at = timezone.now()
                rule.save()
                log_event(request.user, "privacy.retention_approved", rule, {"category": rule.category})
                return redirect("cafeteria:privacy_administration")
    return render(request, "cafeteria/privacy_administration.html", {
        "notice_form": notice_form, "rule_form": rule_form,
        "ready": privacy_ready(), "rules": RetentionRule.objects.all(),
        "requests": DataRequest.objects.order_by("resolved_at", "due_at")[:200],
        "blocked": BlockedData.objects.order_by("destroy_after")[:100],
    })


def export_candidate(item, actor):
    data = {"request": str(item.id)}
    if item.requester:
        data["account"] = {"name": item.requester.get_full_name(), "email": item.requester.email}
    if item.student and item.requester and FamilyMembership.objects.filter(user=item.requester, family_id=item.student.family_id).exists():
        student = item.student
        data["student"] = {key: str(getattr(student, key) or "") for key in (
            "first_name", "last_name", "birth_date", "contact_phone", "contact_email", "contact_notes",
        )}
        if medical_access(actor, student):
            data["health"] = {key: str(getattr(student, key) or "") for key in (
                "allergy_title", "allergy_details", "kitchen_instructions", "allergy_review_status",
            )}
        data["bookings"] = list(student.bookings.values("date", "diet_name", "unit_price", "status"))
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


@privacy_staff
@transaction.atomic
def privacy_request_review(request, request_id):
    item = get_object_or_404(DataRequest.objects.select_for_update(), pk=request_id)
    form = RequestReviewForm(request.POST or None, initial={"export_text": export_candidate(item, request.user) if item.kind == DataRequest.Kind.ACCESS else ""})
    if request.method == "POST" and form.is_valid():
        if item.resolved_at:
            return HttpResponseForbidden(_("La sol·licitud ja està resolta."))
        action = form.cleaned_data["action"]
        if action != "respond":
            if not item.student:
                form.add_error("action", _("Aquesta acció requereix seleccionar un infant. Per a baixes de comptes o famílies, documenta la tramitació al procediment de drets."))
        export_text = form.cleaned_data["export_text"]
        if export_text:
            try:
                payload = json.loads(export_text)
                content = json.dumps(payload, ensure_ascii=False, indent=2).encode()
                if len(content) > 10 * 1024 * 1024:
                    raise ValueError
            except (ValueError, TypeError):
                form.add_error("export_text", _("L'exportació ha de ser JSON vàlid de menys de 10 MB."))
        if not form.errors:
            if action != "respond":
                restrict_student(item.student, actor=request.user, category="health" if action == "restrict_health" else "operational")
            if export_text:
                item.export_file.save(f"{uuid.uuid4().hex}.json", ContentFile(content), save=False)
                item.export_expires_at = timezone.now() + timedelta(days=7)
            item.response = form.cleaned_data["response"]
            item.resolved_at = timezone.now()
            item.reviewed_by = request.user
            item.save()
            log_event(request.user, "privacy.request_resolved", item)
            return redirect("cafeteria:privacy_administration")
    return render(request, "cafeteria/privacy_request_review.html", {"item": item, "form": form})


@privacy_staff
@require_POST
def reserved_data_access(request, record_id):
    item = get_object_or_404(BlockedData, pk=record_id)
    import re
    reference = request.POST.get("reference", "")
    if not re.fullmatch(r"[A-Za-z0-9/-]{3,80}", reference) or request.POST.get("authority_purpose") != "on":
        return HttpResponseForbidden(_("Indica una referència d'expedient i confirma la finalitat legal del desbloqueig."))
    # Keep the case reference in dedicated audit metadata, without clinical payload.
    from .models import AuditEvent
    AuditEvent.objects.create(actor=request.user, action="privacy.reserved_access", target_type=item._meta.label,
                              target_id=str(item.pk), details={"case_reference": reference})
    if item.file_name and request.POST.get("document") == "on":
        return FileResponse(default_storage.open(item.file_name, "rb"), as_attachment=True, filename="document-reservat")
    return HttpResponse(json.dumps(item.payload, ensure_ascii=False), content_type="application/json")


@login_required
@sensitive_post_parameters("password")
def privacy_roles(request):
    if not user_has_role(request.user, Role.ADMIN):
        return HttpResponseForbidden(_("Només l'administració pot gestionar autoritzacions."))
    form = RoleGrantForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if not request.user.check_password(form.cleaned_data["password"]):
            form.add_error("password", _("Contrasenya incorrecta."))
        else:
            user = form.cleaned_data["user"]
            group, _created = Group.objects.get_or_create(name=form.cleaned_data["role"])
            if form.cleaned_data["grant"]:
                user.groups.add(group)
            else:
                user.groups.remove(group)
            log_event(request.user, "privacy.role_granted" if form.cleaned_data["grant"] else "privacy.role_revoked", user, {"category": group.name})
            return redirect("cafeteria:privacy_roles")
    return render(request, "cafeteria/security_form.html", {"form": form, "title": _("Autoritzacions específiques")})


@login_required
def backup_custody(request):
    if not user_has_role(request.user, Role.ADMIN):
        return HttpResponseForbidden(_("Només l'administració pot custodiar còpies."))
    if request.method == "POST":
        try:
            backup_id = uuid.UUID(request.POST.get("backup_id", ""))
        except (ValueError, TypeError):
            raise Http404
        item = get_object_or_404(BackupCustody, pk=backup_id)
        if request.POST.get("action") == "delete":
            item.deleted_at = timezone.now()
        elif not item.deleted_at and item.expires_at > timezone.now() and request.POST.get("outside_server") == "on" and request.POST.get("separate_keys") == "on":
            item.confirmed_at = timezone.now()
            item.confirmed_by = request.user
        else:
            return HttpResponseForbidden(_("Confirma la còpia fora del VPS i la custòdia separada de les claus."))
        item.save()
        log_event(request.user, "privacy.backup_custody_updated", item)
        return redirect("cafeteria:backup_custody")
    return render(request, "cafeteria/backup_custody.html", {"copies": BackupCustody.objects.order_by("-generated_at")[:100], "overdue": backup_overdue()})


@login_required
@require_POST
def restriction_ledger_download(request):
    if not user_has_role(request.user, Role.ADMIN) and not explicit_role(request.user, Role.PRIVACY):
        return HttpResponseForbidden(_("No tens permís per custodiar el registre de restriccions."))
    output = io.BytesIO()
    encrypt_stream(io.BytesIO(json.dumps(load_restriction_ledger()).encode()), output, purpose="backup", context=b"restriction-ledger")
    output.seek(0)
    log_event(request.user, "privacy.restriction_ledger_downloaded", None)
    return FileResponse(output, as_attachment=True, filename="restriccions-actuals.afaenc")


@login_required
@sensitive_post_parameters("password", "token")
@transaction.atomic
def mfa_setup(request):
    if TOTPDevice.objects.filter(user=request.user, confirmed=True).exists():
        return redirect("cafeteria:mfa_verify")
    device = TOTPDevice.objects.select_for_update().filter(user=request.user, confirmed=False).first()
    form = MFAForm(request.POST or None) if device else MFABeginForm(request.POST or None)
    codes = None
    if request.method == "POST" and form.is_valid():
        _key, allowed = consume_attempt("mfa-setup", str(request.user.pk))
        if not allowed:
            return HttpResponse(_("Massa intents. Torna-ho a provar més tard."), status=429)
        if device:
            if device.verify_token(form.cleaned_data["token"]):
                device.confirmed = True
                device.save(update_fields=["confirmed"])
                codes = [secrets.token_hex(10) for _ in range(8)]
                RecoveryCode.objects.filter(user=request.user).delete()
                RecoveryCode.objects.bulk_create([RecoveryCode(user=request.user, digest=make_password(code)) for code in codes])
                otp_login(request, device)
                request.session["mfa_verified_at"] = timezone.now().timestamp()
                log_event(request.user, "security.mfa_enabled", request.user)
            else:
                form.add_error("token", _("Codi incorrecte o ja utilitzat."))
        elif request.user.check_password(form.cleaned_data["password"]):
            TOTPDevice.objects.create(user=request.user, name="AFA Ordis", confirmed=False)
            return redirect("cafeteria:mfa_setup")
        else:
            form.add_error("password", _("Contrasenya incorrecta."))
    return render(request, "cafeteria/security_form.html", {
        "title": _("Configura el segon factor"), "form": form,
        "otp_uri": device.config_url if device and not codes else None,
        "recovery_codes": codes,
    })


@login_required
@sensitive_post_parameters("token")
@transaction.atomic
def mfa_verify(request):
    device = TOTPDevice.objects.select_for_update().filter(user=request.user, confirmed=True).first()
    if not device:
        return redirect("cafeteria:mfa_setup")
    form = MFAForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        _key, allowed = consume_attempt("mfa", str(request.user.pk))
        if not allowed:
            return HttpResponse(_("Massa intents. Torna-ho a provar més tard."), status=429)
        token = form.cleaned_data["token"].strip()
        verified = device.verify_token(token) if token.isdigit() and len(token) == 6 else False
        if not verified and len(token) == 20:
            for code in RecoveryCode.objects.select_for_update().filter(user=request.user, used_at__isnull=True):
                if check_password(token, code.digest):
                    code.used_at = timezone.now()
                    code.save(update_fields=["used_at"])
                    verified = True
                    break
        if verified:
            otp_login(request, device)
            request.session["mfa_verified_at"] = timezone.now().timestamp()
            log_event(request.user, "security.mfa_verified", request.user)
            return redirect("cafeteria:dashboard")
        form.add_error("token", _("Codi incorrecte o ja utilitzat."))
    return render(request, "cafeteria/security_form.html", {"form": form, "title": _("Verifica el segon factor")})


@login_required
def kitchen_report(request):
    if not explicit_role(request.user, Role.KITCHEN):
        return HttpResponseForbidden(_("Cal autorització de cuina."))
    from .services import bookings_for_day, teacher_bookings_for_day
    today = timezone.localdate()
    rows = []
    for booking in bookings_for_day(today):
        student = booking.student
        hold = student.meal_safety_hold
        rows.append({"name": student.full_name, "group": student.course_group.name if student.course_group else "",
                     "diet": "" if hold else booking.diet_name, "instructions": "" if hold else student.kitchen_instructions,
                     "hold": hold, "pending": student.allergy_review_status == "pending"})
    for booking in teacher_bookings_for_day(today):
        rows.append({"name": booking.teacher.full_name, "group": _("Personal docent"), "diet": booking.diet_name})
    log_event(request.user, "privacy.operational_report_viewed", None, {"count": len(rows)})
    return render(request, "cafeteria/kitchen_report.html", {"rows": rows, "today": today})
