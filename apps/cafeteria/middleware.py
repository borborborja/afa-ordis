from django.conf import settings
from django.middleware.locale import LocaleMiddleware
from django.utils import translation
from django.db.models import prefetch_related_objects
from django.utils.cache import add_never_cache_headers
from django.http import HttpResponse
from django.utils.deprecation import MiddlewareMixin

from .maintenance import PortalBusy, portal_lock


class PortalMaintenanceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        exclusive = request.method == "POST" and request.path.endswith(("/gestio/portal/restaura/", "/gestio/portal/copia/"))
        try:
            with portal_lock(exclusive=exclusive):
                return self.get_response(request)
        except PortalBusy:
            response = HttpResponse(translation.gettext("El portal està ocupat. Torna-ho a provar en uns instants."), status=503)
            response["Retry-After"] = "5"
            add_never_cache_headers(response)
            return response


class PortalPrivacyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            prefetch_related_objects([request.user], "groups")
        response = self.get_response(request)
        if request.user.is_authenticated or "/comptes/" in request.path or "/invitacions/" in request.path:
            add_never_cache_headers(response)
            response["Referrer-Policy"] = "no-referrer"
        return response


class PortalAccessMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        from django.shortcuts import redirect, render
        from .privacy import privileged, privacy_ready, restore_marker
        name = request.resolver_match.url_name if request.resolver_match else ""
        if settings.DATA_ENCRYPTION_ENABLED and restore_marker().exists():
            response = HttpResponse(translation.gettext("Restauració pendent de completar amb el registre de restriccions actual. Contacta amb l'administració."), status=503)
            add_never_cache_headers(response)
            return response
        public = {"privacy_notice", "healthcheck", "login", "logout", "password_reset", "password_reset_done", "password_reset_confirm", "password_reset_complete", "set_language", "web_app_manifest", "web_app_service_worker"}
        if name in public:
            return None
        if settings.MFA_REQUIRED and privileged(request.user) and name not in {"mfa_setup", "mfa_verify"}:
            from django.utils import timezone
            verified_at = request.session.get("mfa_verified_at", 0)
            if not request.user.is_verified() or timezone.now().timestamp() - verified_at > 12 * 3600:
                return redirect("cafeteria:mfa_verify")
        setup = {"mfa_setup", "mfa_verify", "privacy_roles", "privacy_administration", "privacy_center", "privacy_request_review", "withdraw_health_consent", "reserved_data_access", "backup_custody", "restriction_ledger_download", "portal_backup_download", "portal_restore", "navigation_preferences", "dashboard_preferences"}
        if settings.PRIVACY_ENFORCED and request.method == "POST" and name not in setup and not privacy_ready():
            return render(request, "cafeteria/privacy_pending.html", status=503)


class PortalLocaleMiddleware(LocaleMiddleware):
    """Resolve portal language without letting the browser override Catalan by default."""

    def process_request(self, request):
        supported = {code for code, _label in settings.LANGUAGES}
        language = translation.get_language_from_path(request.path_info)
        if language not in supported:
            language = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        if language not in supported:
            user = getattr(request, "user", None)
            try:
                language = user.profile.language if user and user.is_authenticated else None
            except Exception:  # a profile may not exist during a migration/bootstrap
                language = None
        translation.activate(language if language in supported else settings.LANGUAGE_CODE)
        request.LANGUAGE_CODE = translation.get_language()
