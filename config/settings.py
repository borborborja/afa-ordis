import os
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-development-key" if DEBUG else "")
if not DEBUG and (len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5 or SECRET_KEY.startswith("django-insecure-")):
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must contain at least 50 random characters in production.")
ALLOWED_HOSTS = [host.strip() for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()]
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")
app_url = urlsplit(APP_BASE_URL)
if not DEBUG and (app_url.scheme != "https" or not app_url.hostname or app_url.path or app_url.query or app_url.fragment or app_url.username):
    raise ImproperlyConfigured("APP_BASE_URL must be an HTTPS origin, without a path or credentials.")
if not DEBUG and (not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS):
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must list the portal's host names explicitly.")
CSRF_TRUSTED_ORIGINS = [APP_BASE_URL] if APP_BASE_URL else []

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.cafeteria",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "apps.cafeteria.middleware.PortalMaintenanceMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.cafeteria.middleware.PortalPrivacyMiddleware",
    "apps.cafeteria.middleware.PortalLocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.cafeteria.middleware.PortalAccessMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "apps.cafeteria.context_processors.role_flags",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATA_ENCRYPTION_ENABLED = env_bool("DATA_ENCRYPTION_ENABLED", not DEBUG)
ENCRYPTION_KEY_FILE = os.getenv("ENCRYPTION_KEY_FILE", "/run/secrets/afa_keys")
PRIVATE_TEMP_DIR = os.getenv("PRIVATE_TEMP_DIR", "/tmp")
DATABASE_ENGINE = os.getenv("DATABASE_ENGINE", "config.sqlcipher" if DATA_ENCRYPTION_ENABLED else "django.db.backends.sqlite3")
if not DEBUG and (not DATA_ENCRYPTION_ENABLED or DATABASE_ENGINE != "config.sqlcipher"):
    raise ImproperlyConfigured("Production requires SQLCipher and encrypted file storage.")
DATABASES = {
    "default": {
        "ENGINE": DATABASE_ENGINE,
        "NAME": os.getenv("DATABASE_NAME", "/data/afa-ordis.sqlite3"),
        "OPTIONS": {"timeout": 30, "transaction_mode": "IMMEDIATE"},
        "TEST": {"NAME": os.getenv("DATABASE_TEST_NAME")},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTHENTICATION_BACKENDS = ["apps.cafeteria.auth.CaseInsensitiveEmailBackend"]

LANGUAGE_CODE = "ca"
LANGUAGES = [("ca", "Català"), ("es", "Castellà")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = os.getenv("TIME_ZONE", "Europe/Madrid")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "apps.cafeteria.crypto.EncryptedStorage" if DATA_ENCRYPTION_ENABLED else "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        "OPTIONS": {"file_permissions_mode": 0o644, "directory_permissions_mode": 0o755},
    },
}
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", "/data/media"))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.getenv("SMTP_HOST", "")
EMAIL_PORT = int(os.getenv("SMTP_PORT", "587"))
EMAIL_HOST_USER = os.getenv("SMTP_USERNAME", "")
EMAIL_HOST_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_USE_TLS = env_bool("SMTP_USE_TLS", True)
EMAIL_USE_SSL = env_bool("SMTP_USE_SSL", False)
if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ImproperlyConfigured("Select SMTP STARTTLS or implicit TLS, not both.")
if not DEBUG and not (EMAIL_USE_TLS or EMAIL_USE_SSL):
    raise ImproperlyConfigured("Production SMTP requires TLS.")
EMAIL_TIMEOUT = int(os.getenv("SMTP_TIMEOUT", "15"))
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "Menjador AFA Ordis <noreply@localhost>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

LOGIN_URL = "cafeteria:login"
LOGIN_REDIRECT_URL = "cafeteria:dashboard"
LOGOUT_REDIRECT_URL = "cafeteria:login"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = not DEBUG
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "3600")) if not DEBUG else 0
# The portal owns one hostname, not every subdomain or browser preload policy.
# HTTPS, HSTS, secret-key, cookie and all other deployment checks remain enabled.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]
SECURE_REFERRER_POLICY = "strict-origin"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
FILE_UPLOAD_PERMISSIONS = 0o600
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o700
DATA_UPLOAD_MAX_NUMBER_FILES = 20
AUTH_RATE_LIMIT = 10
AUTH_RATE_WINDOW = 300
FILE_UPLOAD_TEMP_DIR = PRIVATE_TEMP_DIR
PRIVACY_ENFORCED = not DEBUG
BACKUP_CUSTODY_DAYS = 1
BACKUP_RETENTION_DAYS = 30
DEFAULT_EXCEPTION_REPORTER_FILTER = "apps.cafeteria.logging.PrivacyExceptionReporterFilter"
if not DEBUG:
    LOGGING = {
        "version": 1, "disable_existing_loggers": False,
        "filters": {"private": {"()": "apps.cafeteria.logging.MetadataOnlyFilter"}},
        "formatters": {"metadata": {"format": "{asctime} {levelname} {message}", "style": "{"}},
        "handlers": {"console": {"class": "logging.StreamHandler", "filters": ["private"], "formatter": "metadata"}},
        "root": {"handlers": ["console"], "level": "WARNING"},
        "loggers": {"django": {"handlers": ["console"], "level": "WARNING", "propagate": False}},
    }
