from django.conf.urls.i18n import i18n_patterns
from django.urls import include, path

from apps.cafeteria import views as cafeteria_views

urlpatterns = [
    path("health/", cafeteria_views.healthcheck, name="healthcheck"),
    path("i18n/", include("django.conf.urls.i18n")),
    path("manifest.webmanifest", cafeteria_views.web_app_manifest, name="web_app_manifest"),
    path("service-worker.js", cafeteria_views.web_app_service_worker, name="web_app_service_worker"),
]
urlpatterns += i18n_patterns(
    path("", include("apps.cafeteria.urls")),
)
