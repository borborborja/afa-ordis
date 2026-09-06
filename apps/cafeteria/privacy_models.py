import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class PrivacyNotice(models.Model):
    version = models.CharField(max_length=40, unique=True, verbose_name=_("Versió"))
    controller = models.CharField(max_length=200, verbose_name=_("Nom legal de l'AFA"))
    tax_id = models.CharField(max_length=32, verbose_name=_("NIF"))
    address = models.CharField(max_length=255, verbose_name=_("Adreça legal"))
    contact_email = models.EmailField(verbose_name=_("Contacte de privacitat"))
    text_ca = models.TextField(verbose_name=_("Política en català"))
    text_es = models.TextField(verbose_name=_("Política en castellà"))
    health_text_ca = models.TextField(verbose_name=_("Consentiment explícit de salut en català"))
    health_text_es = models.TextField(verbose_name=_("Consentiment explícit de salut en castellà"))
    contracts_verified = models.BooleanField(default=False, verbose_name=_("Contractes i transferències verificats"))
    assessment_approved = models.BooleanField(default=False, verbose_name=_("Avaluació d'impacte i bases jurídiques validades"))
    recovery_verified = models.BooleanField(default=False, verbose_name=_("Restauració i custòdia de claus verificades"))
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def current(cls):
        return cls.objects.filter(published_at__isnull=False).order_by("-published_at", "-pk").first()

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk, published_at__isnull=False).exists():
            raise ValidationError(_("Una política publicada és immutable. Crea una versió nova."))
        return super().save(*args, **kwargs)


class RetentionRule(models.Model):
    class Category(models.TextChoices):
        HEALTH = "health", _("Salut, després de la baixa o rectificació")
        OPERATIONAL = "operational", _("Dades operatives, després de la baixa")
        ACCOUNTING = "accounting", _("Comptabilitat, després del tancament")
        AUDIT = "audit", _("Registres de seguretat, des de la creació")
        RIGHTS = "rights", _("Sol·licituds de drets, després de resoldre-les")
        CONSENT = "consent", _("Consentiments, després de la retirada o baixa")

    category = models.CharField(max_length=20, choices=Category.choices, unique=True, verbose_name=_("Categoria"))
    days = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(36500)], verbose_name=_("Dies de conservació"))
    justification = models.TextField(verbose_name=_("Justificació i norma aplicable"))
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    approved_at = models.DateTimeField(default=timezone.now)


class ConsentRecord(models.Model):
    student = models.ForeignKey("cafeteria.Student", on_delete=models.PROTECT, related_name="health_consents")
    representative = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    notice = models.ForeignKey(PrivacyNotice, on_delete=models.PROTECT)
    granted_at = models.DateTimeField(auto_now_add=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    authority_confirmed = models.BooleanField(default=False)


class DataRequest(models.Model):
    class Kind(models.TextChoices):
        ACCESS = "access", _("Accés / portabilitat")
        RECTIFY = "rectify", _("Rectificació")
        ERASE = "erase", _("Supressió")
        RESTRICT = "restrict", _("Limitació / oposició")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    kind = models.CharField(max_length=12, choices=Kind.choices, verbose_name=_("Dret que vols exercir"))
    student = models.ForeignKey("cafeteria.Student", null=True, blank=True, on_delete=models.SET_NULL, verbose_name=_("Infant, si escau"))
    message = models.TextField(max_length=3000, verbose_name=_("Detall de la sol·licitud"))
    created_at = models.DateTimeField(auto_now_add=True)
    due_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    response = models.TextField(blank=True)
    export_file = models.FileField(upload_to="privacy_exports/", blank=True)
    export_expires_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_data_requests")


class BlockedData(models.Model):
    """Reserved evidence, never included in ordinary portal queries or exports."""
    subject = models.UUIDField(db_index=True)
    category = models.CharField(max_length=20, choices=RetentionRule.Category.choices)
    payload = models.JSONField(default=dict)
    file_name = models.CharField(max_length=255, blank=True)
    blocked_at = models.DateTimeField(default=timezone.now)
    destroy_after = models.DateTimeField()
    legal_hold = models.BooleanField(default=False)


class RestrictionEvent(models.Model):
    """Also exported to a separate encrypted ledger, to survive old-backup restores."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subject = models.UUIDField()
    category = models.CharField(max_length=20)
    created_at = models.DateTimeField(default=timezone.now)
    destroy_after = models.DateTimeField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)


class BackupCustody(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    generated_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    expires_at = models.DateTimeField()
    deleted_at = models.DateTimeField(null=True, blank=True)


class RecoveryCode(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    digest = models.CharField(max_length=128)
    used_at = models.DateTimeField(null=True, blank=True)
