from django import forms
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _

from .models import DataRequest, PrivacyNotice, RetentionRule, Role, Student


class NoticeForm(forms.ModelForm):
    class Meta:
        model = PrivacyNotice
        exclude = ("approved_by", "published_at", "created_at")

    def clean(self):
        data = super().clean()
        if not all(data.get(key) for key in ("contracts_verified", "assessment_approved", "recovery_verified")):
            raise forms.ValidationError(_("Cal validar contractes, bases jurídiques, avaluació d'impacte i recuperació abans de publicar."))
        for key in ("text_ca", "text_es", "health_text_ca", "health_text_es"):
            if len(data.get(key, "").strip()) < 100 or "[PENDENT" in data.get(key, ""):
                self.add_error(key, _("Completa el text i elimina els marcadors pendents abans de publicar."))
        return data


class RetentionForm(forms.ModelForm):
    class Meta:
        model = RetentionRule
        fields = ("category", "days", "justification")


class DataRequestForm(forms.ModelForm):
    class Meta:
        model = DataRequest
        fields = ("kind", "student", "message")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = Student.objects.filter(family__memberships__user=user).distinct()
        self.fields["message"].help_text = _("No hi incloguis contrasenyes ni documents d'identitat. Verificarem la representació si cal.")


class RequestReviewForm(forms.Form):
    response = forms.CharField(label=_("Resposta a la persona sol·licitant"), widget=forms.Textarea)
    export_text = forms.CharField(required=False, label=_("Exportació revisada en format JSON"), widget=forms.Textarea(attrs={"rows": 16}))
    reviewed = forms.BooleanField(label=_("He verificat la identitat, la representació i que no es revelen dades indegudes de tercers"))
    action = forms.ChoiceField(label=_("Acció"), choices=(
        ("respond", _("Publica la resposta")),
        ("restrict_health", _("Bloqueja les dades de salut de l'infant")),
        ("restrict_student", _("Dona de baixa i bloqueja les dades de l'infant")),
    ))


class RoleGrantForm(forms.Form):
    user = forms.ModelChoiceField(queryset=User.objects.filter(is_active=True), label=_("Persona autoritzada"))
    role = forms.ChoiceField(choices=[(role.value, role.label) for role in (Role.KITCHEN, Role.HEALTH_REVIEWER, Role.PRIVACY)], label=_("Permís específic"))
    grant = forms.BooleanField(required=False, label=_("Concedeix el permís (desmarca per revocar)"))
    password = forms.CharField(widget=forms.PasswordInput, label=_("Contrasenya actual"))


class MFAForm(forms.Form):
    token = forms.CharField(max_length=64, label=_("Codi de l'autenticador o codi de recuperació"), widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "autofocus": True}))


class MFABeginForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput, label=_("Contrasenya actual"))
