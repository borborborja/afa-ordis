from __future__ import annotations

import secrets
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import formats, timezone
from django.utils.translation import gettext_lazy as _


class Role(models.TextChoices):
    ADMIN = "admin", _("Administració")
    MANAGER = "manager", _("Gestió de menjador")
    TUTOR = "tutor", _("Persona tutora")
    TEACHER = "teacher", _("Personal docent")
    KITCHEN = "kitchen", _("Cuina")
    HEALTH_REVIEWER = "health_reviewer", _("Revisió documental de salut")
    PRIVACY = "privacy", _("Responsable de privacitat")


class FamilyBookingView(models.TextChoices):
    TABS = "tabs", _("Una pestanya per infant")
    MATRIX = "matrix", _("Matriu compartida")


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
    return bool(user.is_authenticated and user.is_active and (user.is_superuser or any(group.name in roles for group in user.groups.all())))


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    language = models.CharField(max_length=2, choices=[("ca", _("Català")), ("es", _("Castellà"))], default="ca")
    receive_operational_emails = models.BooleanField(default=True)
    navigation_state = models.JSONField(default=dict, blank=True)
    dashboard_widgets = models.JSONField(default=list, blank=True)
    can_submit_expenses = models.BooleanField(default=False)
    family_booking_view = models.CharField(
        max_length=12,
        choices=FamilyBookingView.choices,
        default=FamilyBookingView.TABS,
    )

    def __str__(self) -> str:
        return self.user.get_full_name() or self.user.email


class PortalSettings(models.Model):
    """Single shared setting for links that are useful to every account."""

    school_menu_url = models.URLField(default="https://agora.xtec.cat/esc-mariapages-ordis/")
    allow_family_student_creation = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="updated_portal_settings",
    )

    class Meta:
        verbose_name = "Configuració del portal"

    def clean(self):
        if self.pk and type(self).objects.exclude(pk=self.pk).exists():
            raise ValidationError(_("Només pot existir una configuració del portal."))

    def __str__(self) -> str:
        return "Configuració del portal"


class AcademicYear(models.Model):
    name = models.CharField(max_length=9, unique=True, help_text=_("Exemple: 2026-2027"))
    starts_on = models.DateField()
    ends_on = models.DateField()
    is_active = models.BooleanField(default=False)

    class Meta:
        ordering = ["-starts_on"]

    def clean(self):
        if self.ends_on is None or self.starts_on is None:
            return
        if self.ends_on <= self.starts_on:
            raise ValidationError(_("La data final ha de ser posterior a la inicial."))
        if type(self).objects.exclude(pk=self.pk).filter(starts_on__lte=self.ends_on, ends_on__gte=self.starts_on).exists():
            raise ValidationError(_("Les dates del curs se superposen amb un altre curs acadèmic."))

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
    name = models.CharField(max_length=160, help_text=_("Nom identificatiu de la família"))
    phone = models.CharField(max_length=32, blank=True)
    monthly_email_enabled = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def recipient_emails(self) -> list[str]:
        emails = self.memberships.filter(
            user__profile__receive_operational_emails=True,
            user__is_active=True,
        ).exclude(user__email="").values_list("user__email", flat=True)
        return sorted(set(emails))


class FamilyMembership(models.Model):
    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="family_memberships")
    label = models.CharField(max_length=80, blank=True, help_text=_("Exemple: persona tutora o contacte principal"))
    is_primary_contact = models.BooleanField(default=False)
    onboarding_required = models.BooleanField(default=False)
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["family", "user"], name="unique_family_membership")]

    def __str__(self) -> str:
        return f"{self.user.email} · {self.family.name}"


class AfaMembershipStatus(models.TextChoices):
    PENDING = "pending", _("Pendent de cobrament")
    PAID = "paid", _("Pagada")
    EXEMPT = "exempt", _("Exempta")


class AfaPaymentMethod(models.TextChoices):
    TRANSFER = "transfer", _("Transferència")
    CASH = "cash", _("Efectiu")
    CARD = "card", _("Targeta")
    OTHER = "other", _("Altres")


class AfaFeeSettings(models.Model):
    """The single annual AFA fee; dining prices stay entirely separate."""

    academic_year = models.OneToOneField(AcademicYear, on_delete=models.CASCADE, related_name="afa_fee_settings")
    amount = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(Decimal("0.00"))])
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="updated_afa_fee_settings",
    )

    def __str__(self):
        return f"Quota AFA {self.academic_year} · {self.amount} €"


class AfaMembership(models.Model):
    """An optional family membership for one academic year."""

    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name="afa_memberships")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="afa_memberships")
    status = models.CharField(max_length=12, choices=AfaMembershipStatus.choices, default=AfaMembershipStatus.PENDING)
    amount = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(Decimal("0.00"))])
    paid_on = models.DateField(null=True, blank=True)
    payment_method = models.CharField(max_length=16, choices=AfaPaymentMethod.choices, blank=True)
    payment_reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="updated_afa_memberships",
    )

    class Meta:
        ordering = ["-academic_year__starts_on", "family__name"]
        constraints = [models.UniqueConstraint(fields=["family", "academic_year"], name="unique_afa_membership_per_year")]

    def clean(self):
        if self.status == AfaMembershipStatus.PAID and not self.paid_on:
            raise ValidationError(_("Cal indicar la data de cobrament d'una quota pagada."))

    def __str__(self):
        return f"{self.family} · quota AFA {self.academic_year}"


class FinancialAccountType(models.TextChoices):
    BANK = "bank", _("Compte bancari")
    CASH = "cash", _("Efectiu")
    OTHER = "other", _("Altres")


class EconomicEntryType(models.TextChoices):
    INCOME = "income", _("Ingrés")
    EXPENSE = "expense", _("Despesa")


class EconomicReviewStatus(models.TextChoices):
    SUBMITTED = "submitted", _("Pendent de revisar")
    APPROVED = "approved", _("Aprovada")
    REJECTED = "rejected", _("Rebutjada")
    WITHDRAWN = "withdrawn", _("Retirada")


class EconomicPaymentStatus(models.TextChoices):
    PENDING = "pending", _("Pendent de pagament")
    PAID = "paid", _("Pagada")


class EconomicSettings(models.Model):
    """Single shared configuration for the simple AFA cash ledger."""

    allow_all_users_expense_submissions = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="updated_economic_settings",
    )

    def clean(self):
        if self.pk and type(self).objects.exclude(pk=self.pk).exists():
            raise ValidationError(_("Només pot existir una configuració econòmica."))

    def __str__(self):
        return "Configuració econòmica"


class FinancialAccount(models.Model):
    name = models.CharField(max_length=100, unique=True)
    account_type = models.CharField(max_length=12, choices=FinancialAccountType.choices, default=FinancialAccountType.BANK)
    opening_balance = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    opening_balance_date = models.DateField(default=timezone.localdate)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class EconomicCategory(models.Model):
    name = models.CharField(max_length=100)
    entry_type = models.CharField(max_length=10, choices=EconomicEntryType.choices)
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["entry_type", "sort_order", "name"]
        constraints = [models.UniqueConstraint(fields=["name", "entry_type"], name="unique_economic_category_type")]

    def __str__(self):
        return self.name


class EconomicEntry(models.Model):
    entry_type = models.CharField(max_length=10, choices=EconomicEntryType.choices)
    date = models.DateField(default=timezone.localdate)
    concept = models.CharField(max_length=180)
    category = models.ForeignKey(EconomicCategory, on_delete=models.PROTECT, related_name="entries")
    account = models.ForeignKey(FinancialAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="entries")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    notes = models.TextField(blank=True)
    review_status = models.CharField(max_length=12, choices=EconomicReviewStatus.choices, default=EconomicReviewStatus.APPROVED)
    payment_status = models.CharField(max_length=12, choices=EconomicPaymentStatus.choices, default=EconomicPaymentStatus.PAID)
    paid_on = models.DateField(null=True, blank=True)
    rejected_reason = models.TextField(blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="submitted_economic_entries"
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_economic_entries"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": _("L'import ha de ser superior a zero.")})
        if self.category_id and self.entry_type and self.category.entry_type != self.entry_type:
            raise ValidationError({"category": _("La categoria no correspon al tipus de moviment.")})
        if self.review_status == EconomicReviewStatus.APPROVED and not self.account_id:
            raise ValidationError({"account": _("Cal indicar el compte de l'AFA.")})
        if self.review_status == EconomicReviewStatus.REJECTED and not self.rejected_reason.strip():
            raise ValidationError({"rejected_reason": _("Cal indicar el motiu del rebuig.")})
        if self.payment_status == EconomicPaymentStatus.PAID and not self.paid_on:
            self.paid_on = self.date

    @property
    def affects_balance(self):
        return self.review_status == EconomicReviewStatus.APPROVED and self.payment_status == EconomicPaymentStatus.PAID

    def __str__(self):
        return f"{self.get_entry_type_display()} · {self.concept} · {self.amount} €"


def economic_attachment_path(instance, filename):
    suffix = Path(filename).suffix.lower()[:12]
    return f"economia/{instance.entry_id}/{uuid.uuid4().hex}{suffix}"


class EconomicAttachment(models.Model):
    entry = models.ForeignKey(EconomicEntry, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=economic_attachment_path)
    original_name = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return self.original_name


class MealPlan(models.TextChoices):
    FIXED = "fixed", _("Fix")
    SPORADIC = "sporadic", _("Esporàdic")


class AllergyReviewStatus(models.TextChoices):
    PENDING = "pending", _("Pendent de validar")
    APPROVED = "approved", _("Validada")
    REJECTED = "rejected", _("Rebutjada")


def allergy_document_path(instance, filename):
    """Keep medical files outside publicly meaningful paths."""
    suffix = Path(filename).suffix.lower()[:12]
    return f"alergies/{uuid.uuid4().hex}{suffix}"


class Student(models.Model):
    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name="students")
    course_group = models.ForeignKey(CourseGroup, null=True, blank=True, on_delete=models.SET_NULL, related_name="students")
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=120)
    birth_date = models.DateField(null=True, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=32, blank=True)
    contact_notes = models.TextField(blank=True)
    default_diet = models.ForeignKey(Diet, null=True, blank=True, on_delete=models.PROTECT, related_name="students")
    dietary_notes = models.TextField(blank=True)
    has_allergy = models.BooleanField(null=True, default=None)
    allergy_title = models.CharField(max_length=160, blank=True)
    allergy_details = models.TextField(blank=True)
    kitchen_instructions = models.TextField(blank=True, verbose_name=_("Instruccions alimentàries per a cuina"))
    safety_hold = models.BooleanField(default=False)
    privacy_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    inactive_since = models.DateTimeField(null=True, blank=True)
    allergy_document = models.FileField(upload_to=allergy_document_path, blank=True)
    allergy_document_name = models.CharField(max_length=255, blank=True)
    allergy_review_status = models.CharField(max_length=12, choices=AllergyReviewStatus.choices, blank=True)
    allergy_rejection_reason = models.TextField(blank=True)
    allergy_reviewed_at = models.DateTimeField(null=True, blank=True)
    allergy_reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="reviewed_student_allergies",
    )
    is_scholarship = models.BooleanField(default=False, verbose_name=_("Ajut de menjador"))
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

    def save(self, *args, **kwargs):
        if not self.active and self.inactive_since is None:
            self.inactive_since = timezone.now()
            if kwargs.get("update_fields"):
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"inactive_since"}
        elif self.active and self.inactive_since is not None:
            self.inactive_since = None
            if kwargs.get("update_fields"):
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"inactive_since"}
        return super().save(*args, **kwargs)

    @property
    def meal_safety_hold(self):
        from .privacy import has_health_consent
        return bool(self.safety_hold or (settings.PRIVACY_ENFORCED and (self.has_allergy is None or not self.default_diet_id)) or (self.has_allergy and (
            not has_health_consent(self) or self.allergy_review_status == "rejected"
            or (settings.PRIVACY_ENFORCED and not self.kitchen_instructions.strip())
        )))

    @property
    def has_operational_allergy_alert(self) -> bool:
        """Pending declarations are shown too, so no real allergy is missed."""
        return bool(
            self.safety_hold or self.has_allergy
        )

    def __str__(self) -> str:
        return self.full_name


class TeacherMealProfile(models.Model):
    """Dining profile for a member of the teaching staff, never tied to a family."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="teacher_meal_profile")
    default_diet = models.ForeignKey(Diet, on_delete=models.PROTECT, related_name="teacher_profiles")
    meal_plan = models.CharField(max_length=12, choices=MealPlan.choices, default=MealPlan.SPORADIC)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__first_name", "user__last_name", "user__email"]

    @property
    def full_name(self):
        return self.user.get_full_name() or self.user.email

    def __str__(self):
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
        if not self.academic_year_id or self.date is None:
            return
        if not self.academic_year.starts_on <= self.date <= self.academic_year.ends_on:
            raise ValidationError(_("El dia ha de ser dins del curs acadèmic."))

    def __str__(self) -> str:
        return f"{self.date:%d/%m/%Y} · {'Servei' if self.is_service_day else 'Sense servei'}"


class CourseClosure(models.Model):
    course_group = models.ForeignKey(CourseGroup, on_delete=models.CASCADE, related_name="closures")
    date = models.DateField()
    title = models.CharField(max_length=160, default=_("Excursió"))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "course_group"]
        constraints = [models.UniqueConstraint(fields=["course_group", "date"], name="unique_course_closure")]

    def clean(self):
        if not self.course_group_id or self.date is None:
            return
        if not self.course_group.academic_year.starts_on <= self.date <= self.course_group.academic_year.ends_on:
            raise ValidationError(_("L'excursió ha de ser dins del curs acadèmic."))

    def __str__(self) -> str:
        return f"{self.title} · {self.course_group.name} · {self.date:%d/%m/%Y}"


class AcademicHolidayType(models.TextChoices):
    GENERAL = "general", _("Festiu general")
    LOCAL = "local", _("Festiu local")
    SCHOOL = "school", _("Festiu de centre")


class AcademicHoliday(models.Model):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="holidays")
    title = models.CharField(max_length=160)
    holiday_type = models.CharField(max_length=12, choices=AcademicHolidayType.choices, default=AcademicHolidayType.GENERAL)
    starts_on = models.DateField()
    ends_on = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["starts_on", "ends_on", "title"]

    def clean(self):
        if not self.academic_year_id or self.starts_on is None or self.ends_on is None:
            return
        if self.ends_on < self.starts_on:
            raise ValidationError(_("La data final no pot ser anterior a la inicial."))
        if not self.academic_year.starts_on <= self.starts_on <= self.academic_year.ends_on:
            raise ValidationError(_("L'inici del festiu ha de ser dins del curs acadèmic."))
        if not self.academic_year.starts_on <= self.ends_on <= self.academic_year.ends_on:
            raise ValidationError(_("El final del festiu ha de ser dins del curs acadèmic."))

    def __str__(self):
        return f"{self.get_holiday_type_display()} · {self.title} · {self.starts_on:%d/%m/%Y}"


class AcademicIntensivePeriod(models.Model):
    """Informative intensive-school-day period shown in academic calendars."""

    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="intensive_periods"
    )
    title = models.CharField(max_length=160, default=_("Jornada intensiva"))
    starts_on = models.DateField()
    ends_on = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["starts_on", "ends_on", "title"]

    def clean(self):
        if not self.academic_year_id or self.starts_on is None or self.ends_on is None:
            return
        if self.ends_on < self.starts_on:
            raise ValidationError(_("La data final no pot ser anterior a la inicial."))
        if not self.academic_year.starts_on <= self.starts_on <= self.academic_year.ends_on:
            raise ValidationError(_("L'inici de la jornada intensiva ha de ser dins del curs acadèmic."))
        if not self.academic_year.starts_on <= self.ends_on <= self.academic_year.ends_on:
            raise ValidationError(_("El final de la jornada intensiva ha de ser dins del curs acadèmic."))

    def __str__(self):
        return f"{self.title} · {self.starts_on:%d/%m/%Y}"


class AcademicNoticeLevel(models.TextChoices):
    INFORMATION = "information", _("Informació")
    ALERT = "alert", _("Alerta")


class AcademicNotice(models.Model):
    """An informational calendar event that never changes meal availability."""

    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="notices"
    )
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    level = models.CharField(
        max_length=16, choices=AcademicNoticeLevel.choices,
        default=AcademicNoticeLevel.INFORMATION,
    )
    starts_on = models.DateField()
    ends_on = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["starts_on", "ends_on", "title"]

    def clean(self):
        if not self.academic_year_id or self.starts_on is None or self.ends_on is None:
            return
        if self.ends_on < self.starts_on:
            raise ValidationError(_("La data final no pot ser anterior a la inicial."))
        if not self.academic_year.starts_on <= self.starts_on <= self.academic_year.ends_on:
            raise ValidationError(_("L'inici de la incidència ha de ser dins del curs acadèmic."))
        if not self.academic_year.starts_on <= self.ends_on <= self.academic_year.ends_on:
            raise ValidationError(_("El final de la incidència ha de ser dins del curs acadèmic."))

    def __str__(self):
        return f"{self.get_level_display()} · {self.title} · {self.starts_on:%d/%m/%Y}"


class MealSettings(models.Model):
    academic_year = models.OneToOneField(AcademicYear, on_delete=models.CASCADE, related_name="meal_settings")
    daily_cutoff = models.TimeField(null=True, blank=True, help_text=_("Sense valor: les famílies poden modificar reserves fins al final del dia."))
    daily_report_send_time = models.TimeField(null=True, blank=True, help_text=_("Sense valor: no s'envia cap informe diari automàtic."))
    monthly_preparation_day = models.PositiveSmallIntegerField(default=1)
    monthly_preparation_hour = models.TimeField(default="08:00")
    daily_reports_enabled = models.BooleanField(default=False)
    monthly_statements_enabled = models.BooleanField(default=True)

    def clean(self):
        if self.monthly_preparation_day is not None and not 1 <= self.monthly_preparation_day <= 28:
            raise ValidationError(_("El dia de preparació mensual ha d'estar entre 1 i 28."))

    def __str__(self) -> str:
        return f"Configuració de menjador · {self.academic_year}"


class DailyReportRecipient(models.Model):
    settings = models.ForeignKey(MealSettings, on_delete=models.CASCADE, related_name="daily_recipients")
    name = models.CharField(max_length=100, blank=True)
    email = models.EmailField()
    active = models.BooleanField(default=True)
    user = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.CASCADE)

    class Meta:
        ordering = ["email"]
        constraints = [models.UniqueConstraint(fields=["settings", "email"], name="unique_daily_recipient")]

    def __str__(self) -> str:
        return self.email


class PriceRule(models.Model):
    scholarship = models.BooleanField(default=False)
    meal_plan = models.CharField(max_length=12, choices=MealPlan.choices)
    effective_from = models.DateField()
    amount = models.DecimalField(max_digits=7, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))])
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_price_rules")

    class Meta:
        ordering = ["-effective_from", "scholarship", "meal_plan"]
        constraints = [models.UniqueConstraint(fields=["scholarship", "meal_plan", "effective_from"], name="unique_price_effectivity")]

    def __str__(self) -> str:
        benefit = "Amb ajut de menjador" if self.scholarship else "Sense ajut de menjador"
        return f"{benefit} · {self.get_meal_plan_display()} · {self.amount} € des de {self.effective_from:%d/%m/%Y}"

    @classmethod
    def amount_for_category(cls, scholarship: bool, meal_plan: str, service_date: date) -> Decimal | None:
        rule = cls.objects.filter(
            scholarship=scholarship,
            meal_plan=meal_plan,
            effective_from__lte=service_date,
        ).order_by("-effective_from").first()
        return rule.amount if rule else None

    @classmethod
    def amount_for(cls, student: Student, service_date: date) -> Decimal | None:
        return cls.amount_for_category(student.is_scholarship, student.meal_plan, service_date)


class BookingStatus(models.TextChoices):
    ACTIVE = "active", _("Activa")
    CANCELLED = "cancelled", _("Anul·lada")


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
        return super().clean()

    def save(self, *args, **kwargs):
        if self.diet and not self.diet_name:
            self.diet_name = self.diet.name
        if self.unit_price is None and self.status == BookingStatus.ACTIVE:
            self.unit_price = PriceRule.amount_for(self.student, self.date)
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.student} · {self.date:%d/%m/%Y}"


class TeacherMealBooking(models.Model):
    teacher = models.ForeignKey(TeacherMealProfile, on_delete=models.CASCADE, related_name="bookings")
    date = models.DateField()
    diet = models.ForeignKey(Diet, null=True, blank=True, on_delete=models.SET_NULL, related_name="teacher_bookings")
    diet_name = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=12, choices=BookingStatus.choices, default=BookingStatus.ACTIVE)
    unit_price = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_teacher_bookings")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="updated_teacher_bookings")
    override_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date", "teacher__user__first_name", "teacher__user__last_name"]
        constraints = [models.UniqueConstraint(fields=["teacher", "date"], name="unique_teacher_meal_booking")]

    def save(self, *args, **kwargs):
        if self.diet and not self.diet_name:
            self.diet_name = self.diet.name
        if self.unit_price is None and self.status == BookingStatus.ACTIVE:
            self.unit_price = PriceRule.amount_for_category(False, self.teacher.meal_plan, self.date)
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.teacher} · {self.date:%d/%m/%Y}"


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
    PREPARED = "prepared", _("Preparat")
    CLOSED = "closed", _("Tancat")
    SENT = "sent", _("Enviat")


class MonthlyPreparation(models.Model):
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["year", "month"], name="unique_monthly_preparation")]


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
        return formats.date_format(date(self.year, self.month, 1), "F Y")

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


class TeacherMonthlyStatement(models.Model):
    teacher = models.ForeignKey(TeacherMealProfile, on_delete=models.CASCADE, related_name="statements")
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=12, choices=StatementStatus.choices, default=StatementStatus.PREPARED)
    total = models.DecimalField(max_digits=9, decimal_places=2, default=Decimal("0.00"))
    prepared_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="closed_teacher_statements")
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-year", "-month", "teacher__user__first_name"]
        constraints = [models.UniqueConstraint(fields=["teacher", "year", "month"], name="unique_teacher_monthly_statement")]

    def __str__(self):
        return f"{self.teacher} · {self.month:02d}/{self.year}"


class TeacherStatementLine(models.Model):
    statement = models.ForeignKey(TeacherMonthlyStatement, on_delete=models.CASCADE, related_name="lines")
    service_date = models.DateField()
    diet_name = models.CharField(max_length=80, blank=True)
    meal_plan = models.CharField(max_length=12, choices=MealPlan.choices)
    unit_price = models.DecimalField(max_digits=7, decimal_places=2)

    class Meta:
        ordering = ["service_date"]
        constraints = [models.UniqueConstraint(fields=["statement", "service_date"], name="unique_teacher_statement_line")]


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
            raise ValidationError(_("Una invitació de tutor necessita una família."))
        if self.role != Role.TUTOR and self.family:
            raise ValidationError(_("Només les persones tutores es vinculen a una família."))

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


class FamilyImportBatch(models.Model):
    """A short-lived, reviewed CSV import.  The source file is never stored."""

    class Status(models.TextChoices):
        PREVIEW = "preview", _("Pendent de confirmar")
        IMPORTED = "imported", _("Importat")
        EXPIRED = "expired", _("Caducat")

    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="family_imports")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="family_imports"
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PREVIEW)
    source_digest = models.CharField(max_length=64)
    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.JSONField(default=list, blank=True)
    errors = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    imported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_confirmable(self) -> bool:
        return self.status == self.Status.PREVIEW and self.expires_at > timezone.now() and bool(self.valid_rows)

    def __str__(self) -> str:
        return f"Importació {self.created_at:%d/%m/%Y %H:%M}"


def log_event(actor, action: str, target, details: dict | None = None) -> AuditEvent:
    # Audit actions/IDs, not clinical content, addresses, tokens or financial notes.
    safe = {key: value for key, value in (details or {}).items() if key in {
        "status", "enabled", "count", "after_cutoff", "field_names", "category", "version",
    } and isinstance(value, (bool, int, float, list, str))}
    return AuditEvent.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        target_type=target._meta.label if hasattr(target, "_meta") else type(target).__name__,
        target_id=str(getattr(target, "pk", "")),
        details=safe,
    )


from .privacy_models import (  # noqa: E402, F401 -- register split model definitions
    BackupCustody, BlockedData, ConsentRecord, DataRequest, PrivacyNotice,
    RecoveryCode, RetentionRule, RestrictionEvent,
)
