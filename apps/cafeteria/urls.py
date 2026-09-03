from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views

app_name = "cafeteria"

urlpatterns = [
    path("health/", views.healthcheck, name="healthcheck"),
    path("", views.dashboard, name="dashboard"),
    path("comptes/entrada/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("comptes/sortida/", auth_views.LogoutView.as_view(), name="logout"),
    path("comptes/contrasenya/", auth_views.PasswordResetView.as_view(
        template_name="registration/password_reset_form.html",
        email_template_name="registration/password_reset_email.txt",
        subject_template_name="registration/password_reset_subject.txt",
        success_url=reverse_lazy("cafeteria:password_reset_done"),
    ), name="password_reset"),
    path("comptes/contrasenya/enviada/", auth_views.PasswordResetDoneView.as_view(template_name="registration/password_reset_done.html"), name="password_reset_done"),
    path("comptes/contrasenya/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="registration/password_reset_confirm.html",
        success_url=reverse_lazy("cafeteria:password_reset_complete"),
    ), name="password_reset_confirm"),
    path("comptes/contrasenya/feta/", auth_views.PasswordResetCompleteView.as_view(template_name="registration/password_reset_complete.html"), name="password_reset_complete"),
    path("families/<int:family_id>/menjador/", views.family_calendar, name="family_calendar"),
    path("families/<int:family_id>/menjador/actualitza/", views.bulk_booking, name="bulk_booking"),
    path("alumnes/<int:student_id>/edita/", views.student_edit, name="student_edit"),
    path("invitacions/nova/", views.invitation_create, name="invitation_create"),
    path("invitacions/<str:token>/", views.invitation_accept, name="invitation_accept"),
    path("gestio/tarifes/", views.price_rules, name="price_rules"),
    path("gestio/informes-diaris/", views.daily_reports, name="daily_reports"),
    path("gestio/informes-diaris/<str:service_date>/envia/", views.daily_report_send, name="daily_report_send"),
    path("resums/", views.monthly_statements, name="monthly_statements"),
    path("resums/prepara/", views.statement_prepare, name="statement_prepare"),
    path("resums/<int:statement_id>/", views.statement_detail, name="statement_detail"),
    path("resums/<int:statement_id>/tanca/", views.statement_close, name="statement_close"),
    path("resums/<int:statement_id>/envia/", views.statement_send, name="statement_send"),
    path("resums/<int:statement_id>/csv/", views.statement_csv, name="statement_csv"),
    path("gestio/auditoria/", views.audit_log, name="audit_log"),
]
