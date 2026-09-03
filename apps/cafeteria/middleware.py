from django.utils import translation


class ProfileLocaleMiddleware:
    """Use the member's saved language unless they explicitly changed it this session."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated and "django_language" not in request.session:
            try:
                language = user.profile.language
            except Exception:  # a profile may not exist during a migration/bootstrap
                language = None
            if language:
                request.session["django_language"] = language
                translation.activate(language)
                request.LANGUAGE_CODE = language
        return self.get_response(request)
