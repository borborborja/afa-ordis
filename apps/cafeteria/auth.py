import hashlib

from django.conf import settings
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.http import HttpResponse
from django.utils.translation import gettext as _


def consume_attempt(scope, identity, limit=None):
    """Fixed-window counters shared by threads in the supported single web worker."""
    key = f"auth:{scope}:{hashlib.sha256(identity.strip().casefold().encode()).hexdigest()}"
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
        identity = request.POST.get("username", "").strip().lower()
        self.attempt_key, allowed = consume_attempt("login", identity)
        if not allowed:
            response = HttpResponse(_("Massa intents. Espera uns minuts abans de tornar-ho a provar."), status=429)
            response["Retry-After"] = str(settings.AUTH_RATE_WINDOW)
            return response
        # Accounts created by invitations and bootstrap use lowercase email usernames.
        request.POST = request.POST.copy()
        request.POST["username"] = identity
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        cache.delete(self.attempt_key)
        return super().form_valid(form)
