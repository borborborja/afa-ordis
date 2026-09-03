from django import forms
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _

from .models import (
    AcademicYear,
    CourseClosure,
    CourseGroup,
    DailyReportRecipient,
    Diet,
    Family,
    Invitation,
    MealSettings,
    MealPlan,
    PriceRule,
    Role,
    Student,
)


class InvitationForm(forms.ModelForm):
    class Meta:
        model = Invitation
        fields = ("email", "role", "family")

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        family = cleaned.get("family")
        if role == Role.TUTOR and not family:
            self.add_error("family", _("Cal seleccionar una família per convidar un tutor."))
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


class TutorStudentForm(forms.ModelForm):
    class Meta:
        model = Student
        exclude = ("family", "is_scholarship", "active", "created_at", "updated_at")
        widgets = {
            "birth_date": forms.DateInput(attrs={"type": "date"}),
            "contact_notes": forms.Textarea(attrs={"rows": 3}),
            "dietary_notes": forms.Textarea(attrs={"rows": 3}),
        }


class FamilyForm(forms.ModelForm):
    class Meta:
        model = Family
        fields = ("name", "billing_email", "phone", "address", "monthly_email_enabled", "active")
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}


class StaffStudentForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = (
            "family", "course_group", "first_name", "last_name", "birth_date", "contact_email",
            "contact_phone", "contact_notes", "default_diet", "dietary_notes", "is_scholarship",
            "meal_plan", "active",
        )
        widgets = {
            "birth_date": forms.DateInput(attrs={"type": "date"}),
            "contact_notes": forms.Textarea(attrs={"rows": 3}),
            "dietary_notes": forms.Textarea(attrs={"rows": 3}),
        }


class AcademicYearForm(forms.ModelForm):
    class Meta:
        model = AcademicYear
        fields = ("name", "starts_on", "ends_on", "is_active")
        widgets = {
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
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
            "daily_cutoff", "daily_reports_enabled", "monthly_preparation_day",
            "monthly_preparation_hour", "monthly_statements_enabled",
        )
        widgets = {
            "daily_cutoff": forms.TimeInput(attrs={"type": "time"}),
            "monthly_preparation_hour": forms.TimeInput(attrs={"type": "time"}),
        }


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
        widgets = {"effective_from": forms.DateInput(attrs={"type": "date"})}


class BookingBulkForm(forms.Form):
    student_id = forms.IntegerField()
    dates = forms.CharField(widget=forms.HiddenInput())
    action = forms.ChoiceField(choices=[("add", "Apuntar a dinar"), ("cancel", "Anul·lar")])
    diet_id = forms.IntegerField(required=False)
    override_reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
