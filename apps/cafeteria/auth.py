import hashlib

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.http import HttpResponse
from django.utils.translation import gettext as _

from .identity import normalize_email


class CaseInsensitiveEmailBackend(ModelBackend):
    """Authenticate portal accounts by their email address, independent of casing."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        user_model = get_user_model()
        email = normalize_email(username or kwargs.get(user_model.USERNAME_FIELD))
        if not email or password is None:
            return None
        try:
            user = user_model._default_manager.get(email__iexact=email)
        except (user_model.DoesNotExist, user_model.MultipleObjectsReturned):
            # Keep the failed-login response timing comparable to a real account.
            user_model().set_password(password)
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None


def consume_attempt(scope, identity, limit=None):
    """Fixed-window counters shared by threads in the supported single web worker."""
    key = f"auth:{scope}:{hashlib.sha256(normalize_email(identity).encode()).hexdigest()}"
    cache.add(key, 0, settings.AUTH_RATE_WINDOW)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.add(key, 1, settings.AUTH_RATE_WINDOW)
        count = 1
    return key, count <= (limit or settings.AUTH_RATE_LIMIT)


class PortalLoginView(LoginView):
    template_name = "registration/login.html"

    def post(self, request, *args, **kwargs):
        identity = normalize_email(request.POST.get("username"))
        self.attempt_key, allowed = consume_attempt("login", identity)
        if not allowed:
            response = HttpResponse(_("Massa intents. Espera uns minuts abans de tornar-ho a provar."), status=429)
            response["Retry-After"] = str(settings.AUTH_RATE_WINDOW)
            return response
        # The authentication backend resolves the canonical email, not username casing.
        request.POST = request.POST.copy()
        request.POST["username"] = identity
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        cache.delete(self.attempt_key)
        return super().form_valid(form)
