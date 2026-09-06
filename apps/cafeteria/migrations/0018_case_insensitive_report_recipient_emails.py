from django.db import migrations


INDEX_NAME = "cafeteria_dailyrecipient_email_ci_unique"


def normalize_existing_recipients(apps, schema_editor):
    recipient_model = apps.get_model("cafeteria", "DailyReportRecipient")
    alias = schema_editor.connection.alias
    recipients = list(recipient_model.objects.using(alias).only("pk", "settings_id", "email"))
    seen = set()
    for recipient in recipients:
        email = recipient.email.strip().casefold()
        identity = (recipient.settings_id, email)
        if identity in seen:
            raise RuntimeError(
                "Cannot enable case-insensitive report recipients because duplicate "
                "addresses exist. Resolve the duplicate recipients and retry the migration."
            )
        seen.add(identity)
        if recipient.email != email:
            recipient.email = email
    recipient_model.objects.using(alias).bulk_update(recipients, ["email"])


def create_recipient_index(apps, schema_editor):
    recipient_model = apps.get_model("cafeteria", "DailyReportRecipient")
    quote = schema_editor.quote_name
    table = quote(recipient_model._meta.db_table)
    settings_column = quote(recipient_model._meta.get_field("settings").column)
    email = quote(recipient_model._meta.get_field("email").column)
    index = quote(INDEX_NAME)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"CREATE UNIQUE INDEX {index} ON {table} ({settings_column}, LOWER({email}))"
        )


def drop_recipient_index(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"DROP INDEX {schema_editor.quote_name(INDEX_NAME)}")


class Migration(migrations.Migration):
    dependencies = [("cafeteria", "0017_case_insensitive_account_emails")]

    operations = [
        migrations.RunPython(normalize_existing_recipients, migrations.RunPython.noop),
        migrations.RunPython(create_recipient_index, drop_recipient_index),
    ]
