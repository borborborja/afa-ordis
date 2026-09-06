from pathlib import Path

from django import forms
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .models import (
    AcademicHoliday,
    AcademicIntensivePeriod,
    AcademicNotice,
    AcademicYear,
    AllergyReviewStatus,
    AfaFeeSettings,
    AfaMembership,
    CourseClosure,
    CourseGroup,
    DailyReportRecipient,
    Diet,
    EconomicCategory,
    EconomicEntry,
    EconomicEntryType,
    EconomicPaymentStatus,
    EconomicReviewStatus,
    EconomicSettings,
    Family,
    FinancialAccount,
    Invitation,
    MealSettings,
    MealPlan,
    PortalSettings,
    PriceRule,
    Role,
    Student,
    TeacherMealProfile,
)


MEDICAL_DOCUMENT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic"}
MAX_MEDICAL_DOCUMENT_BYTES = 10 * 1024 * 1024


class InvitationForm(forms.ModelForm):
    class Meta:
        model = Invitation
        fields = ("email", "role", "family")
        labels = {"email": _("Correu electrònic"), "role": _("Tipus d'accés"), "family": _("Família")}

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        family = cleaned.get("family")
        if role == Role.TUTOR and not family:
            self.add_error("family", _("Cal seleccionar una família per convidar una persona tutora."))
        if role != Role.TUTOR and family:
            self.add_error("family", _("Les invitacions de personal no es vinculen a una família."))
        return cleaned


class InvitationAcceptanceForm(SetPasswordForm):
    first_name = forms.CharField(max_length=150, required=True, label=_("Nom"))
    last_name = forms.CharField(max_length=150, required=True, label=_("Cognoms"))

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        user.email = user.email.lower()
        user.username = user.email
        if commit:
            user.save()
        return user


class StudentAllergyFormMixin(forms.ModelForm):
    """Shared, family-safe allergy declaration fields for a student profile."""

    allergy_declaration = forms.ChoiceField(
        choices=(
            ("", _("Selecciona una opció")),
            ("no", _("No té al·lèrgies declarades")),
            ("yes", _("Sí, té una al·lèrgia")),
        ),
        required=False,
        label=_("Declaració d’al·lèrgies"),
        widget=forms.RadioSelect,
        help_text=_("Si hi ha una al·lèrgia, cal aportar un document mèdic que la justifiqui."),
    )

    def __init__(self, *args, require_profile_completion=False, **kwargs):
        self.require_profile_completion = require_profile_completion
        super().__init__(*args, **kwargs)
        self._previous_allergy = {
            "has_allergy": self.instance.has_allergy,
            "title": self.instance.allergy_title,
            "details": self.instance.allergy_details,
            "document": self.instance.allergy_document.name if self.instance.allergy_document else "",
            "status": self.instance.allergy_review_status,
        }
        self.fields["allergy_declaration"].required = require_profile_completion
        allergy_key = self.prefix or "student"
        self.fields["allergy_declaration"].widget.attrs["data-allergy-declaration"] = allergy_key
        if self.instance.has_allergy is True:
            self.fields["allergy_declaration"].initial = "yes"
        elif self.instance.has_allergy is False:
            self.fields["allergy_declaration"].initial = "no"
        self.fields["allergy_title"].help_text = _("Exemple: Al·lèrgia a fruits secs.")
        self.fields["allergy_details"].help_text = _("Indica la informació necessària perquè el menjador pugui actuar amb seguretat.")
        self.fields["allergy_document"].help_text = _("Obligatori si hi ha al·lèrgia. PDF o imatge, màxim 10 MB.")
        self.fields["allergy_document"].widget.attrs.update({
            "accept": ".pdf,image/jpeg,image/png,image/webp,image/heic",
        })
        for field_name in ("allergy_title", "allergy_details", "allergy_document"):
            self.fields[field_name].widget.attrs["data-allergy-field"] = allergy_key
        if require_profile_completion:
            self.fields["birth_date"].required = True

    def clean_allergy_document(self):
        uploaded = self.cleaned_data.get("allergy_document")
        if not uploaded or uploaded is False:
            return uploaded
        extension = Path(uploaded.name).suffix.lower()
        if extension not in MEDICAL_DOCUMENT_EXTENSIONS:
            raise ValidationError(_("El document mèdic ha de ser un PDF o una imatge compatible."))
        if uploaded.size > MAX_MEDICAL_DOCUMENT_BYTES:
            raise ValidationError(_("El document mèdic supera els 10 MB."))
        return uploaded

    def clean(self):
        cleaned = super().clean()
        declaration = cleaned.get("allergy_declaration", "")
        if not declaration:
            if self.require_profile_completion:
                self.add_error("allergy_declaration", _("Indica si l’alumne/a té alguna al·lèrgia."))
            return cleaned

        has_allergy = declaration == "yes"
        cleaned["has_allergy"] = has_allergy
        if not has_allergy:
            return cleaned

        if not cleaned.get("allergy_title", "").strip():
            self.add_error("allergy_title", _("Indica un títol de l’al·lèrgia."))
        if not cleaned.get("allergy_details", "").strip():
            self.add_error("allergy_details", _("Explica el detall de l’al·lèrgia."))
        uploaded = cleaned.get("allergy_document")
        requires_new_document = self._previous_allergy["status"] == AllergyReviewStatus.REJECTED
        has_existing_document = bool(self._previous_allergy["document"])
        if uploaded is False or (not uploaded and (not has_existing_document or requires_new_document)):
            self.add_error("allergy_document", _("Adjunta el document mèdic que acredita l’al·lèrgia."))
        return cleaned

    def save(self, commit=True):
        student = super().save(commit=False)
        declaration = self.cleaned_data.get("allergy_declaration", "")
        previous = self._previous_allergy
        previous_document = previous["document"]
        delete_previous_document = False

        if declaration == "no":
            student.has_allergy = False
            student.allergy_title = ""
            student.allergy_details = ""
            student.allergy_document = None
            student.allergy_document_name = ""
            student.allergy_review_status = ""
            student.allergy_rejection_reason = ""
            student.allergy_reviewed_at = None
            student.allergy_reviewed_by = None
            delete_previous_document = bool(previous_document)
        elif declaration == "yes":
            uploaded = self.cleaned_data.get("allergy_document")
            if uploaded and uploaded is not False:
                student.allergy_document_name = Path(uploaded.name).name[:255]
            changed = (
                previous["has_allergy"] is not True
                or previous["title"] != student.allergy_title
                or previous["details"] != student.allergy_details
                or bool(uploaded and uploaded is not False)
            )
            if changed:
                student.has_allergy = True
                student.allergy_review_status = AllergyReviewStatus.PENDING
                student.allergy_rejection_reason = ""
                student.allergy_reviewed_at = None
                student.allergy_reviewed_by = None
                delete_previous_document = bool(previous_document and uploaded and uploaded is not False)

        if commit:
            student.save()
            self.save_m2m()
            if delete_previous_document and previous_document:
                student.allergy_document.storage.delete(previous_document)
        return student


class TutorStudentForm(StudentAllergyFormMixin):
    class Meta:
        model = Student
        fields = (
            "first_name", "last_name", "birth_date", "contact_email", "contact_phone",
            "contact_notes", "default_diet", "dietary_notes", "meal_plan", "allergy_title",
            "allergy_details", "allergy_document",
        )
        widgets = {
            "birth_date": forms.DateInput(attrs={"type": "date"}),
            "contact_notes": forms.Textarea(attrs={"rows": 3}),
            "dietary_notes": forms.Textarea(attrs={"rows": 3}),
            "allergy_details": forms.Textarea(attrs={"rows": 4}),
        }


class FamilyForm(forms.ModelForm):
    class Meta:
        model = Family
        fields = ("name", "billing_email", "phone", "address", "monthly_email_enabled", "active")
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}


class FamilyContactForm(forms.ModelForm):
    """Safe shared-contact editing for people linked to a family."""

    class Meta:
        model = Family
        fields = ("billing_email", "phone", "address", "monthly_email_enabled")
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}
        labels = {
            "billing_email": _("Correu de facturació"),
            "phone": _("Telèfon de contacte"),
            "address": _("Adreça"),
            "monthly_email_enabled": _("Rebre els resums mensuals per correu"),
        }


class FamilyOnboardingContactForm(FamilyContactForm):
    """Initial setup collects a complete shared family contact."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ("billing_email", "phone", "address"):
            self.fields[field_name].required = True


class StaffStudentForm(StudentAllergyFormMixin):
    class Meta:
        model = Student
        fields = (
            "family", "course_group", "first_name", "last_name", "birth_date", "contact_email",
            "contact_phone", "contact_notes", "default_diet", "dietary_notes", "is_scholarship",
            "meal_plan", "active", "allergy_title", "allergy_details", "allergy_document",
        )
        widgets = {
            "birth_date": forms.DateInput(attrs={"type": "date"}),
            "contact_notes": forms.Textarea(attrs={"rows": 3}),
            "dietary_notes": forms.Textarea(attrs={"rows": 3}),
            "allergy_details": forms.Textarea(attrs={"rows": 4}),
        }


class AllergyReviewForm(forms.Form):
    decision = forms.ChoiceField(
        choices=(
            ("approve", _("Valida la declaració")),
            ("reject", _("Rebutja i demana una correcció")),
        ),
        label=_("Decisió"),
        widget=forms.RadioSelect,
    )
    rejection_reason = forms.CharField(
        required=False,
        label=_("Motiu del rebuig"),
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_("Obligatori si rebutges la declaració. La família el veurà a la seva fitxa."),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("decision") == "reject" and not cleaned.get("rejection_reason", "").strip():
            self.add_error("rejection_reason", _("Explica què cal corregir o aportar."))
        return cleaned


class TeacherMealProfileForm(forms.ModelForm):
    class Meta:
        model = TeacherMealProfile
        fields = ("default_diet", "meal_plan", "active", "notes")
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}
        labels = {
            "default_diet": _("Dieta predeterminada"),
            "meal_plan": _("Modalitat"),
            "active": _("Actiu"),
            "notes": _("Observacions"),
        }


class AcademicYearForm(forms.ModelForm):
    class Meta:
        model = AcademicYear
        fields = ("name", "starts_on", "ends_on", "is_active")
        widgets = {
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
        }


class AcademicHolidayForm(forms.ModelForm):
    class Meta:
        model = AcademicHoliday
        fields = ("academic_year", "title", "holiday_type", "starts_on", "ends_on")
        widgets = {
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "academic_year": _("Curs acadèmic"),
            "title": _("Nom del festiu"),
            "holiday_type": _("Tipus de festiu"),
            "starts_on": _("Data inicial"),
            "ends_on": _("Data final"),
        }


class AcademicIntensivePeriodForm(forms.ModelForm):
    class Meta:
        model = AcademicIntensivePeriod
        fields = ("academic_year", "title", "starts_on", "ends_on")
        widgets = {
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "academic_year": _("Curs acadèmic"),
            "title": _("Nom del període"),
            "starts_on": _("Data inicial"),
            "ends_on": _("Data final"),
        }


class AcademicNoticeForm(forms.ModelForm):
    class Meta:
        model = AcademicNotice
        fields = ("academic_year", "title", "description", "level", "starts_on", "ends_on")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {
            "academic_year": _("Curs acadèmic"),
            "title": _("Títol de la incidència"),
            "description": _("Informació per a les famílies"),
            "level": _("Tipus"),
            "starts_on": _("Data inicial"),
            "ends_on": _("Data final"),
        }


class AfaFeeSettingsForm(forms.ModelForm):
    class Meta:
        model = AfaFeeSettings
        fields = ("amount",)
        labels = {"amount": _("Quota anual AFA (€)")}


class AfaMembershipForm(forms.ModelForm):
    class Meta:
        model = AfaMembership
        fields = ("status", "amount", "paid_on", "payment_method", "payment_reference", "notes")
        widgets = {
            "paid_on": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class FinancialAccountForm(forms.ModelForm):
    class Meta:
        model = FinancialAccount
        fields = ("name", "account_type", "opening_balance", "opening_balance_date", "active")
        widgets = {"opening_balance_date": forms.DateInput(attrs={"type": "date"})}
        labels = {
            "name": _("Nom del compte"),
            "account_type": _("Tipus de compte"),
            "opening_balance": _("Saldo inicial (€)"),
            "opening_balance_date": _("Data del saldo inicial"),
            "active": _("Actiu"),
        }


class EconomicCategoryForm(forms.ModelForm):
    class Meta:
        model = EconomicCategory
        fields = ("name", "entry_type", "active", "sort_order")
        labels = {
            "name": _("Nom de la categoria"),
            "entry_type": _("Tipus de moviment"),
            "active": _("Activa"),
            "sort_order": _("Ordre"),
        }


class EconomicSettingsForm(forms.ModelForm):
    class Meta:
        model = EconomicSettings
        fields = ("allow_all_users_expense_submissions",)
        labels = {
            "allow_all_users_expense_submissions": _("Permet que qualsevol persona registrada presenti despeses"),
        }


class EconomicEntryForm(forms.ModelForm):
    class Meta:
        model = EconomicEntry
        fields = ("entry_type", "date", "concept", "category", "account", "amount", "notes", "payment_status", "paid_on")
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "paid_on": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "entry_type": _("Tipus"), "date": _("Data"), "concept": _("Concepte o proveïdor"),
            "category": _("Categoria"), "account": _("Compte de l'AFA"), "amount": _("Import (€)"),
            "notes": _("Observacions"), "payment_status": _("Estat de pagament"),
            "paid_on": _("Data de pagament"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        category_filter = Q(active=True)
        account_filter = Q(active=True)
        if self.instance and self.instance.pk:
            category_filter |= Q(pk=self.instance.category_id)
            account_filter |= Q(pk=self.instance.account_id)
        self.fields["category"].queryset = EconomicCategory.objects.filter(category_filter)
        self.fields["account"].queryset = FinancialAccount.objects.filter(account_filter)

    def clean(self):
        cleaned = super().clean()
        category = cleaned.get("category")
        entry_type = cleaned.get("entry_type")
        if category and entry_type and category.entry_type != entry_type:
            self.add_error("category", _("Selecciona una categoria del mateix tipus que el moviment."))
        return cleaned


class EconomicSubmissionForm(forms.ModelForm):
    class Meta:
        model = EconomicEntry
        fields = ("date", "concept", "category", "amount", "notes")
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "date": _("Data de la despesa"), "concept": _("Concepte o proveïdor"),
            "category": _("Categoria"), "amount": _("Import (€)"), "notes": _("Observacions"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.entry_type = EconomicEntryType.EXPENSE
        self.instance.review_status = EconomicReviewStatus.SUBMITTED
        self.instance.payment_status = EconomicPaymentStatus.PENDING
        self.fields["category"].queryset = EconomicCategory.objects.filter(
            active=True, entry_type=EconomicEntryType.EXPENSE
        )
        labels = {
            "status": _("Estat de la quota"),
            "amount": _("Import de la quota (€)"),
            "paid_on": _("Data de cobrament"),
            "payment_method": _("Mètode de cobrament"),
            "payment_reference": _("Referència"),
            "notes": _("Observacions"),
        }


class CourseGroupForm(forms.ModelForm):
    class Meta:
        model = CourseGroup
        fields = ("academic_year", "name", "sort_order")


class CourseClosureForm(forms.ModelForm):
    class Meta:
        model = CourseClosure
        fields = ("course_group", "date", "title")
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}


class MealSettingsForm(forms.ModelForm):
    class Meta:
        model = MealSettings
        fields = (
            "daily_cutoff", "daily_report_send_time", "daily_reports_enabled", "monthly_preparation_day",
            "monthly_preparation_hour", "monthly_statements_enabled",
        )
        widgets = {
            "daily_cutoff": forms.TimeInput(attrs={"type": "time"}),
            "daily_report_send_time": forms.TimeInput(attrs={"type": "time"}),
            "monthly_preparation_hour": forms.TimeInput(attrs={"type": "time"}),
        }
        labels = {
            "daily_cutoff": _("Hora límit per a canvis de les famílies"),
            "daily_report_send_time": _("Hora d'enviament automàtic del llistat"),
            "daily_reports_enabled": _("Activa l'enviament automàtic del llistat diari"),
            "monthly_preparation_day": _("Dia de preparació dels resums mensuals"),
            "monthly_preparation_hour": _("Hora de preparació dels resums mensuals"),
            "monthly_statements_enabled": _("Activa la preparació automàtica de resums"),
        }


class PortalSettingsForm(forms.ModelForm):
    class Meta:
        model = PortalSettings
        fields = ("school_menu_url",)
        labels = {"school_menu_url": _("Enllaç al menú de l'escola")}
        widgets = {"school_menu_url": forms.URLInput(attrs={"placeholder": "https://…"})}


class DailyReportRecipientForm(forms.ModelForm):
    class Meta:
        model = DailyReportRecipient
        fields = ("name", "email", "active")


class DietForm(forms.ModelForm):
    class Meta:
        model = Diet
        fields = ("name", "description", "active", "sort_order")
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}


class CSVImportForm(forms.Form):
    academic_year = forms.ModelChoiceField(queryset=AcademicYear.objects.all(), label=_("Curs acadèmic"))
    csv_file = forms.FileField(label=_("Fitxer CSV"))

    def clean_csv_file(self):
        file = self.cleaned_data["csv_file"]
        if file.size > 2 * 1024 * 1024:
            raise forms.ValidationError(_("El fitxer no pot superar 2 MB."))
        if not file.name.lower().endswith(".csv"):
            raise forms.ValidationError(_("Cal pujar un fitxer CSV."))
        return file


class PriceRuleForm(forms.ModelForm):
    class Meta:
        model = PriceRule
        fields = ("scholarship", "meal_plan", "effective_from", "amount")
        labels = {
            "scholarship": _("Amb ajut de menjador"),
            "meal_plan": _("Modalitat"),
            "effective_from": _("Vàlida des de"),
            "amount": _("Preu per àpat (€)"),
        }
        widgets = {"effective_from": forms.DateInput(attrs={"type": "date"})}


class BookingBulkForm(forms.Form):
    student_id = forms.IntegerField()
    dates = forms.CharField(widget=forms.HiddenInput())
    action = forms.ChoiceField(choices=[("add", _("Apuntar a dinar")), ("cancel", _("Anul·lar"))])
    diet_id = forms.IntegerField(required=False)
    override_reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
