from django import forms
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _

from .models import Invitation, MealPlan, PriceRule, Role, Student


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
