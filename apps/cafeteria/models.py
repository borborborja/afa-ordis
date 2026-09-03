from __future__ import annotations

import calendar
import secrets
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Role(models.TextChoices):
    ADMIN = "admin", "Administrador"
    MANAGER = "manager", "Gestor de menjador"
    TUTOR = "tutor", "Tutor"


STAFF_ROLES = {Role.ADMIN, Role.MANAGER}


def ensure_role_groups() -> None:
    for role, label in Role.choices:
        Group.objects.get_or_create(name=role)
    # Administradors operatius poden mantenir dades acadèmiques i familiars
    # des del panell de Django, però els gestors no obtenen aquest accés global.
    admin_group = Group.objects.get(name=Role.ADMIN)
    permissions = Permission.objects.filter(content_type__app_label="cafeteria")
    admin_group.permissions.add(*permissions)


def user_has_role(user: User, *roles: Role) -> bool:
    return bool(user.is_authenticated and (user.is_superuser or user.groups.filter(name__in=roles).exists()))


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    language = models.CharField(max_length=2, choices=[("ca", "Català"), ("es", "Castellano")], default="ca")
    receive_operational_emails = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.user.get_full_name() or self.user.email


class AcademicYear(models.Model):
    name = models.CharField(max_length=9, unique=True, help_text="Exemple: 2026-2027")
    starts_on = models.DateField()
    ends_on = models.DateField()
    is_active = models.BooleanField(default=False)

    class Meta:
        ordering = ["-starts_on"]

    def clean(self):
        if self.ends_on <= self.starts_on:
            raise ValidationError("La data final ha de ser posterior a la inicial.")

    def save(self, *args, **kwargs):
        if self.is_active:
            type(self).objects.exclude(pk=self.pk).update(is_active=False)
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class CourseGroup(models.Model):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="course_groups")
    name = models.CharField(max_length=80)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["academic_year", "sort_order", "name"]
        constraints = [models.UniqueConstraint(fields=["academic_year", "name"], name="unique_course_group_per_year")]

    def __str__(self) -> str:
        return f"{self.name} · {self.academic_year}"


class Diet(models.Model):
    name = models.CharField(max_length=80, unique=True)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self) -> str:
        return self.name


class Family(models.Model):
    name = models.CharField(max_length=160, help_text="Nom identificatiu de la família")
    billing_email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    address = models.TextField(blank=True)
    monthly_email_enabled = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def recipient_emails(self) -> list[str]:
        emails = list(
            self.memberships.filter(user__profile__receive_operational_emails=True, user__is_active=True)
            .exclude(user__email="")
            .values_list("user__email", flat=True)
        )
        if self.billing_email:
            emails.append(self.billing_email)
        return sorted(set(emails))


class FamilyMembership(models.Model):
    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="family_memberships")
    label = models.CharField(max_length=80, blank=True, help_text="Exemple: mare, pare, tutor legal")
    is_primary_contact = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["family", "user"], name="unique_family_membership")]

    def __str__(self) -> str:
        return f"{self.user.email} · {self.family.name}"


class MealPlan(models.TextChoices):
    FIXED = "fixed", "Fix"
    SPORADIC = "sporadic", "Esporàdic"


class Student(models.Model):
    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name="students")
    course_group = models.ForeignKey(CourseGroup, null=True, blank=True, on_delete=models.SET_NULL, related_name="students")
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=120)
    birth_date = models.DateField(null=True, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=32, blank=True)
    contact_notes = models.TextField(blank=True)
    default_diet = models.ForeignKey(Diet, null=True, blank=True, on_delete=models.SET_NULL, related_name="students")
    dietary_notes = models.TextField(blank=True)
    is_scholarship = models.BooleanField(default=False, verbose_name="Alumne becat")
    meal_plan = models.CharField(max_length=12, choices=MealPlan.choices, default=MealPlan.FIXED)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["first_name", "last_name"]

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def rate_category(self) -> str:
        return f"{'scholarship' if self.is_scholarship else 'standard'}_{self.meal_plan}"

    def __str__(self) -> str:
        return self.full_name


class ServiceDay(models.Model):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="service_days")
    date = models.DateField()
    is_service_day = models.BooleanField(default=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["date"]
        constraints = [models.UniqueConstraint(fields=["academic_year", "date"], name="unique_service_day")]

    def clean(self):
        if not self.academic_year.starts_on <= self.date <= self.academic_year.ends_on:
            raise ValidationError("El dia ha de ser dins del curs acadèmic.")

    def __str__(self) -> str:
        return f"{self.date:%d/%m/%Y} · {'Servei' if self.is_service_day else 'Sense servei'}"


class CourseClosure(models.Model):
    course_group = models.ForeignKey(CourseGroup, on_delete=models.CASCADE, related_name="closures")
    date = models.DateField()
    title = models.CharField(max_length=160, default="Excursió")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "course_group"]
        constraints = [models.UniqueConstraint(fields=["course_group", "date"], name="unique_course_closure")]

    def clean(self):
        if not self.course_group.academic_year.starts_on <= self.date <= self.course_group.academic_year.ends_on:
            raise ValidationError("L'excursió ha de ser dins del curs acadèmic.")

    def __str__(self) -> str:
        return f"{self.title} · {self.course_group.name} · {self.date:%d/%m/%Y}"


class MealSettings(models.Model):
    academic_year = models.OneToOneField(AcademicYear, on_delete=models.CASCADE, related_name="meal_settings")
    daily_cutoff = models.TimeField(null=True, blank=True, help_text="Sense valor: no s'envia cap informe automàtic.")
    monthly_preparation_day = models.PositiveSmallIntegerField(default=1)
    monthly_preparation_hour = models.TimeField(default="08:00")
    daily_reports_enabled = models.BooleanField(default=False)
    monthly_statements_enabled = models.BooleanField(default=True)

    def clean(self):
        if not 1 <= self.monthly_preparation_day <= 28:
            raise ValidationError("El dia de preparació mensual ha d'estar entre 1 i 28.")

    def __str__(self) -> str:
        return f"Configuració de menjador · {self.academic_year}"


class DailyReportRecipient(models.Model):
    settings = models.ForeignKey(MealSettings, on_delete=models.CASCADE, related_name="daily_recipients")
    name = models.CharField(max_length=100, blank=True)
    email = models.EmailField()
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["email"]
        constraints = [models.UniqueConstraint(fields=["settings", "email"], name="unique_daily_recipient")]

    def __str__(self) -> str:
        return self.email


class PriceRule(models.Model):
    scholarship = models.BooleanField(default=False)
    meal_plan = models.CharField(max_length=12, choices=MealPlan.choices)
    effective_from = models.DateField()
    amount = models.DecimalField(max_digits=7, decimal_places=2, validators=[])
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_price_rules")

    class Meta:
        ordering = ["-effective_from", "scholarship", "meal_plan"]
        constraints = [models.UniqueConstraint(fields=["scholarship", "meal_plan", "effective_from"], name="unique_price_effectivity")]

    def __str__(self) -> str:
        benefit = "Becat" if self.scholarship else "No becat"
        return f"{benefit} · {self.get_meal_plan_display()} · {self.amount} € des de {self.effective_from:%d/%m/%Y}"

    @classmethod
    def amount_for(cls, student: Student, service_date: date) -> Decimal | None:
        rule = cls.objects.filter(
            scholarship=student.is_scholarship,
            meal_plan=student.meal_plan,
            effective_from__lte=service_date,
        ).order_by("-effective_from").first()
        return rule.amount if rule else None


class BookingStatus(models.TextChoices):
    ACTIVE = "active", "Activa"
    CANCELLED = "cancelled", "Anul·lada"


class MealBooking(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="bookings")
    date = models.DateField()
    diet = models.ForeignKey(Diet, null=True, blank=True, on_delete=models.SET_NULL, related_name="bookings")
    diet_name = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=12, choices=BookingStatus.choices, default=BookingStatus.ACTIVE)
    unit_price = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_bookings")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="updated_bookings")
    override_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date", "student__first_name", "student__last_name"]
        constraints = [models.UniqueConstraint(fields=["student", "date"], name="unique_meal_booking")]

    def clean(self):
        if self.student.course_group and CourseClosure.objects.filter(course_group=self.student.course_group, date=self.date).exists():
            raise ValidationError("L'alumne té una excursió aquest dia.")

    def save(self, *args, **kwargs):
        if self.diet and not self.diet_name:
            self.diet_name = self.diet.name
        if self.unit_price is None and self.status == BookingStatus.ACTIVE:
            self.unit_price = PriceRule.amount_for(self.student, self.date)
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.student} · {self.date:%d/%m/%Y}"


class DailyReport(models.Model):
    date = models.DateField(unique=True)
    generated_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    is_outdated = models.BooleanField(default=False)
    recipients = models.JSONField(default=list, blank=True)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="sent_daily_reports")

    class Meta:
        ordering = ["-date"]

    def __str__(self) -> str:
        return f"Informe diari {self.date:%d/%m/%Y}"


class StatementStatus(models.TextChoices):
    PREPARED = "prepared", "Preparat"
    CLOSED = "closed", "Tancat"
    SENT = "sent", "Enviat"


class MonthlyStatement(models.Model):
    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name="statements")
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=12, choices=StatementStatus.choices, default=StatementStatus.PREPARED)
    total = models.DecimalField(max_digits=9, decimal_places=2, default=Decimal("0.00"))
    prepared_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="closed_statements")
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-year", "-month", "family__name"]
        constraints = [models.UniqueConstraint(fields=["family", "year", "month"], name="unique_family_monthly_statement")]

    @property
    def label(self) -> str:
        return f"{calendar.month_name[self.month]} {self.year}"

    def __str__(self) -> str:
        return f"{self.family} · {self.month:02d}/{self.year}"


class StatementLine(models.Model):
    statement = models.ForeignKey(MonthlyStatement, on_delete=models.CASCADE, related_name="lines")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="statement_lines")
    service_date = models.DateField()
    diet_name = models.CharField(max_length=80, blank=True)
    meal_plan = models.CharField(max_length=12, choices=MealPlan.choices)
    scholarship = models.BooleanField(default=False)
    unit_price = models.DecimalField(max_digits=7, decimal_places=2)

    class Meta:
        ordering = ["service_date", "student__first_name"]
        constraints = [models.UniqueConstraint(fields=["statement", "student", "service_date"], name="unique_statement_line")]

    def __str__(self) -> str:
        return f"{self.student} · {self.service_date:%d/%m/%Y}"


class Invitation(models.Model):
    email = models.EmailField()
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.TUTOR)
    family = models.ForeignKey(Family, null=True, blank=True, on_delete=models.CASCADE, related_name="invitations")
    token = models.CharField(max_length=64, unique=True, editable=False)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="created_invitations")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        if self.role == Role.TUTOR and not self.family:
            raise ValidationError("Una invitació de tutor necessita una família.")
        if self.role != Role.TUTOR and self.family:
            raise ValidationError("Només els tutors es vinculen a una família.")

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(days=7)
        return super().save(*args, **kwargs)

    @property
    def is_valid(self) -> bool:
        return self.accepted_at is None and self.expires_at > timezone.now()

    def __str__(self) -> str:
        return f"{self.email} · {self.get_role_display()}"


class AuditEvent(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events")
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=100)
    target_id = models.CharField(max_length=64, blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.created_at:%d/%m/%Y %H:%M} · {self.action}"


def log_event(actor, action: str, target, details: dict | None = None) -> AuditEvent:
    return AuditEvent.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        target_type=target._meta.label if hasattr(target, "_meta") else type(target).__name__,
        target_id=str(getattr(target, "pk", "")),
        details=details or {},
    )
