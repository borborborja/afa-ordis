"""Privacy policy, narrow privileges, reserved evidence and restore-safe restrictions."""
import io
import json
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .crypto import decrypt_stream
from .models import (
    AuditEvent, BackupCustody, BlockedData, ConsentRecord, DataRequest,
    FamilyMembership, PrivacyNotice, RestrictionEvent, RetentionRule, Role, Student,
    log_event,
)

CLINICAL_FIELDS = (
    "allergy_title", "allergy_details", "allergy_rejection_reason", "allergy_document_name",
    "allergy_review_status", "kitchen_instructions", "dietary_notes",
)


def explicit_role(user, *roles):
    return bool(user.is_authenticated and user.is_active and any(group.name in roles for group in user.groups.all()))


def medical_access(user, student=None):
    if not user.is_authenticated or not user.is_active:
        return False
    if student is not None and FamilyMembership.objects.filter(user=user, family_id=student.family_id).exists():
        return True
    return explicit_role(user, Role.HEALTH_REVIEWER)


def privileged(user):
    return user.is_authenticated and (user.is_superuser or explicit_role(
        user, Role.ADMIN, Role.MANAGER, Role.KITCHEN, Role.HEALTH_REVIEWER, Role.PRIVACY,
    ))


def privacy_ready():
    notice = PrivacyNotice.current()
    return bool(notice and notice.contracts_verified and notice.assessment_approved and notice.recovery_verified
                and set(RetentionRule.objects.values_list("category", flat=True)) == set(RetentionRule.Category.values))


def has_health_consent(student):
    if not settings.PRIVACY_ENFORCED:
        return True
    notice = PrivacyNotice.current()
    return bool(notice and student.pk and ConsentRecord.objects.filter(
        student=student, notice__health_text_ca=notice.health_text_ca, notice__health_text_es=notice.health_text_es,
        withdrawn_at__isnull=True, authority_confirmed=True,
    ).exists())


def retention_days(category):
    rule = RetentionRule.objects.filter(category=category).first()
    if not rule:
        raise ValidationError(_("Cal un termini de conservació validat abans de bloquejar o destruir dades."))
    return rule.days


def ledger_path():
    return Path(getattr(settings, "PRIVACY_LEDGER_PATH", Path(settings.DATABASES["default"]["NAME"]).parent / "privacy-ledger.afaenc"))


def restore_marker():
    return ledger_path().with_name(".privacy-restore-pending")


def mark_restore_pending():
    import os
    descriptor = os.open(restore_marker(), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def finish_restore(*, release=False):
    replay_restrictions(load_restriction_ledger())
    if release:
        privacy_maintenance()
        restore_marker().unlink(missing_ok=True)


def merge_ledgers(*ledgers):
    merged = {}
    for ledger in ledgers:
        if not isinstance(ledger, list) or len(ledger) > 10000:
            raise ValueError("Invalid restriction ledger")
        for event in ledger:
            if not isinstance(event, dict) or set(event) != {"id", "subject", "category", "created_at", "destroy_after"}:
                raise ValueError("Invalid restriction event")
            if not all(isinstance(value, str) for value in event.values()):
                raise ValueError("Invalid restriction field types")
            uuid.UUID(event["id"])
            uuid.UUID(event["subject"])
            if event["category"] not in {"health", "operational", "legal_hold", "release_hold", "accounting"}:
                raise ValueError("Invalid restriction category")
            from django.utils.dateparse import parse_datetime
            for key in ("created_at", "destroy_after"):
                parsed = parse_datetime(event[key])
                if parsed is None or timezone.is_naive(parsed):
                    raise ValueError("Invalid restriction date")
            if event["id"] in merged and merged[event["id"]] != event:
                raise ValueError("Conflicting restriction events")
            merged[event["id"]] = event
    return sorted(merged.values(), key=lambda value: (value["created_at"], value["id"]))


def read_external_ledger(source):
    target = io.BytesIO()
    decrypt_stream(source, target, purpose="backup", context=b"restriction-ledger", limit=10 * 1024 * 1024)
    return merge_ledgers(json.loads(target.getvalue()))


def load_restriction_ledger():
    path = ledger_path()
    if path.exists():
        with path.open("rb") as source:
            return read_external_ledger(source)
    if RestrictionEvent.objects.exists():
        raise ValueError("Restriction ledger is missing. Restore the latest separately held ledger before proceeding.")
    return []


def save_restriction_ledger(ledger):
    from .backups import atomic_encrypted_write
    atomic_encrypted_write(ledger_path(), json.dumps(merge_ledgers(ledger)).encode(), context=b"restriction-ledger")


def archive_health(student, *, destroy_after=None):
    """Detach clinical content from all ordinary Student queries; preserve safety hold."""
    destroy_after = destroy_after or timezone.now() + timedelta(days=retention_days("health"))
    payload = {field: getattr(student, field) for field in CLINICAL_FIELDS}
    payload["diet_name"] = student.default_diet.name if student.default_diet_id else ""
    if any(payload.values()) or student.allergy_document:
        BlockedData.objects.create(
            subject=student.privacy_id, category="health", payload=payload,
            file_name=student.allergy_document.name if student.allergy_document else "",
            destroy_after=destroy_after,
        )
    for field in CLINICAL_FIELDS:
        setattr(student, field, "")
    student.allergy_document = ""
    student.allergy_reviewed_at = None
    student.allergy_reviewed_by = None
    student.has_allergy = None
    student.default_diet = None
    student.safety_hold = True
    student.save()
    from .models import MealBooking, StatementLine
    MealBooking.objects.filter(student=student).update(diet=None, diet_name="", override_reason="")
    StatementLine.objects.filter(student=student).update(diet_name="")


def journal_restriction(student, *, category="health", applied=False, destroy_after=None):
    """Call inside an atomic transaction; interruption leaves the portal closed."""
    from .maintenance import portal_lock
    with portal_lock(exclusive=True, name="privacy-ledger"):
        now = timezone.now()
        event = {
            "id": str(uuid.uuid4()), "subject": str(student.privacy_id), "category": category,
            "created_at": now.isoformat(),
            "destroy_after": (destroy_after or now + timedelta(days=retention_days(category))).isoformat(),
        }
        ledger = merge_ledgers(load_restriction_ledger(), [event])
        already_pending = restore_marker().exists()
        mark_restore_pending()
        save_restriction_ledger(ledger)
        if applied:
            RestrictionEvent.objects.create(id=event["id"], subject=student.privacy_id, category=category,
                created_at=now, destroy_after=event["destroy_after"], applied_at=now)
        else:
            replay_restrictions(ledger)
        if not already_pending:
            transaction.on_commit(lambda: restore_marker().unlink(missing_ok=True))
        return ledger


@transaction.atomic
def restrict_student(student, *, actor, category="health", destroy_after=None):
    student = Student.objects.select_for_update().get(pk=student.pk)
    journal_restriction(student, category=category, destroy_after=destroy_after)
    log_event(actor, "privacy.student_restricted", student, {"category": category})


@transaction.atomic
def replay_restrictions(ledger):
    from django.utils.dateparse import parse_datetime
    ledger = merge_ledgers(ledger)
    holds = {}
    for event in ledger:
        if event["category"] in {"legal_hold", "release_hold"}:
            holds[event["subject"]] = event["category"] == "legal_hold"
    for event in ledger:
        record, _ = RestrictionEvent.objects.get_or_create(id=event["id"], defaults={
            "subject": event["subject"], "category": event["category"],
            "created_at": parse_datetime(event["created_at"]), "destroy_after": parse_datetime(event["destroy_after"]),
        })
        if record.applied_at:
            continue
        if record.category in {"legal_hold", "release_hold"}:
            record.applied_at = timezone.now()
            record.save(update_fields=["applied_at"])
            continue
        if record.category == "accounting":
            if any(holds.values()):
                continue  # conservative global pause while any reserved-evidence hold exists
            purge_closed_accounting(record.destroy_after)
            record.applied_at = timezone.now()
            record.save(update_fields=["applied_at"])
            continue
        student = Student.objects.filter(privacy_id=event["subject"]).first()
        if student:
            archive_health(student, destroy_after=record.destroy_after)
            ConsentRecord.objects.filter(student=student, withdrawn_at__isnull=True).update(withdrawn_at=record.created_at)
            if record.category == "operational":
                BlockedData.objects.create(subject=student.privacy_id, category="operational", destroy_after=record.destroy_after,
                    payload={key: str(getattr(student, key) or "") for key in ("first_name", "last_name", "birth_date", "contact_phone", "contact_email", "contact_notes")})
                student.first_name = "[restricted]"
                student.last_name = str(student.privacy_id)[:8]
                student.birth_date = None
                student.contact_phone = student.contact_email = student.contact_notes = ""
                student.active = False
                student.inactive_since = record.created_at
                student.save()
        record.applied_at = timezone.now()
        record.save(update_fields=["applied_at"])
    for subject, hold in holds.items():
        BlockedData.objects.filter(subject=subject).update(legal_hold=hold)
    purge_reserved_data()


def purge_reserved_data():
    for item in BlockedData.objects.filter(destroy_after__lte=timezone.now(), legal_hold=False):
        file_name = item.file_name
        item.delete()
        if file_name and not BlockedData.objects.filter(file_name=file_name).exists() and not Student.objects.filter(allergy_document=file_name).exists():
            transaction.on_commit(lambda name=file_name: default_storage.delete(name))


def purge_closed_accounting(cutoff, *, dry_run=False):
    """Only an explicit, journalled financial-close approval invokes this operation."""
    from .models import AfaMembership, EconomicEntry, MonthlyStatement, TeacherMonthlyStatement
    from .models import MealBooking, TeacherMealBooking
    statements = MonthlyStatement.objects.filter(closed_at__lt=cutoff).exclude(status="prepared")
    teachers = TeacherMonthlyStatement.objects.filter(closed_at__lt=cutoff).exclude(status="prepared")
    entries = EconomicEntry.objects.filter(date__lt=cutoff.date(), paid_on__lt=cutoff.date(),
        updated_at__lt=cutoff, review_status="approved", payment_status="paid")
    memberships = AfaMembership.objects.filter(academic_year__ends_on__lt=cutoff.date(), paid_on__lt=cutoff.date(), status="paid")
    counts = {"statements": statements.count() + teachers.count(), "entries": entries.count(), "memberships": memberships.count()}
    if dry_run:
        return counts
    for statement in statements:
        MealBooking.objects.filter(student__family_id=statement.family_id, date__year=statement.year, date__month=statement.month).delete()
        statement.delete()
    for statement in teachers:
        TeacherMealBooking.objects.filter(teacher_id=statement.teacher_id, date__year=statement.year, date__month=statement.month).delete()
        statement.delete()
    for entry in entries:
        for attachment in entry.attachments.all():
            name = attachment.file.name
            attachment.delete()
            transaction.on_commit(lambda name=name: default_storage.delete(name))
    # Keep non-narrative amounts so historical bank balances do not change.
    entries.update(concept="[retained aggregate]", notes="", rejected_reason="", submitted_by=None, reviewed_by=None)
    memberships.delete()
    return counts


def privacy_maintenance():
    """Small daily batches; deadlines have no invented universal legal duration."""
    from .models import FamilyImportBatch, Invitation, MealBooking, StatementLine, TeacherStatementLine
    now = timezone.now()
    FamilyImportBatch.objects.filter(expires_at__lt=now).update(valid_rows=[], errors=[])
    Invitation.objects.filter(expires_at__lt=now - timedelta(days=30)).delete()
    if settings.DATA_ENCRYPTION_ENABLED:
        replay_restrictions(load_restriction_ledger())
    purge_reserved_data()
    rules = dict(RetentionRule.objects.values_list("category", "days"))
    StatementLine.objects.exclude(diet_name="").update(diet_name="")
    TeacherStatementLine.objects.exclude(diet_name="").update(diet_name="")
    if "health" in rules and settings.DATA_ENCRYPTION_ENABLED:
        for student in Student.objects.filter(active=False, safety_hold=False):
            left_at = student.inactive_since or now
            Student.objects.filter(pk=student.pk, inactive_since__isnull=True).update(inactive_since=left_at)
            restrict_student(student, actor=None, destroy_after=left_at + timedelta(days=rules["health"]))
    if "operational" in rules:
        cutoff = now - timedelta(days=rules["operational"])
        MealBooking.objects.filter(date__lt=cutoff.date()).update(diet=None, diet_name="", override_reason="")
        if settings.DATA_ENCRYPTION_ENABLED:
            for student in Student.objects.filter(active=False, inactive_since__lte=cutoff).exclude(first_name="[restricted]"):
                accounting_days = rules.get("accounting")
                if accounting_days is None or student.statement_lines.filter(
                    statement__closed_at__isnull=True,
                ).exists() or student.statement_lines.filter(
                    statement__closed_at__gte=now - timedelta(days=accounting_days),
                ).exists():
                    continue
                restrict_student(student, actor=None, category="operational", destroy_after=now)
    if "audit" in rules:
        AuditEvent.objects.filter(created_at__lt=now - timedelta(days=rules["audit"])).delete()
    if "consent" in rules:
        ConsentRecord.objects.filter(withdrawn_at__lt=now - timedelta(days=rules["consent"])).delete()
    for req in DataRequest.objects.exclude(export_file="").filter(export_expires_at__lt=now):
        name = req.export_file.name
        req.export_file = ""
        req.save(update_fields=["export_file"])
        default_storage.delete(name)
    if "rights" in rules:
        DataRequest.objects.filter(resolved_at__lt=now - timedelta(days=rules["rights"])).exclude(export_file__gt="").delete()


def backup_overdue():
    now = timezone.now()
    return not BackupCustody.objects.filter(
        confirmed_at__isnull=False, generated_at__gte=now - timedelta(days=settings.BACKUP_CUSTODY_DAYS),
        expires_at__gt=now, deleted_at__isnull=True,
    ).exists()
