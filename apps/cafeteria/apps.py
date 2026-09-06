from django.apps import AppConfig


class CafeteriaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cafeteria"
    verbose_name = "Menjador"

    def ready(self):
        from . import signals  # noqa: F401
        from django.conf import settings
        if settings.DATA_ENCRYPTION_ENABLED:
            from .crypto import keyring
            keyring()
        from django.db.backends.signals import connection_created

        def configure_sqlite(sender, connection, **kwargs):
            if connection.vendor == "sqlite":
                with connection.cursor() as cursor:
                    cursor.execute("PRAGMA journal_mode=WAL;")
                    cursor.execute("PRAGMA foreign_keys=ON;")

        connection_created.connect(configure_sqlite, dispatch_uid="cafeteria.sqlite_pragmas", weak=False)
