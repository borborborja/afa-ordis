from django.conf import settings
from django.middleware.locale import LocaleMiddleware
from django.utils import translation


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
