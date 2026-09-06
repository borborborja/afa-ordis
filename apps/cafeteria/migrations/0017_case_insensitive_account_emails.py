from django.conf import settings
from django.db import migrations


INDEX_NAME = "auth_user_email_ci_unique"


def normalize_existing_emails(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    user_model = apps.get_model(app_label, model_name)
    alias = schema_editor.connection.alias
    users = list(user_model.objects.using(alias).exclude(email="").only("pk", "email"))
    seen = set()
    for user in users:
        email = user.email.strip().casefold()
        if email in seen:
            raise RuntimeError(
                "Cannot enable case-insensitive email identities because duplicate "
                "accounts exist. Resolve the duplicate account emails and retry the migration."
            )
        seen.add(email)
        if user.email != email:
            user.email = email
    user_model.objects.using(alias).bulk_update(users, ["email"])


def create_email_index(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    user_model = apps.get_model(app_label, model_name)
    quote = schema_editor.quote_name
    table = quote(user_model._meta.db_table)
    email = quote(user_model._meta.get_field("email").column)
    index = quote(INDEX_NAME)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"CREATE UNIQUE INDEX {index} ON {table} (LOWER({email})) WHERE {email} <> ''"
        )


def drop_email_index(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"DROP INDEX {schema_editor.quote_name(INDEX_NAME)}")


class Migration(migrations.Migration):
    dependencies = [
        ("cafeteria", "0016_alter_student_default_diet"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(normalize_existing_emails, migrations.RunPython.noop),
        migrations.RunPython(create_email_index, drop_email_index),
    ]
