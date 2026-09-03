from django.apps import AppConfig


class CafeteriaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cafeteria"
    verbose_name = "Menjador"

    def ready(self):
        from . import signals  # noqa: F401
