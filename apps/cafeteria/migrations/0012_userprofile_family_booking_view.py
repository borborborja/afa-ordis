from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cafeteria", "0011_familymembership_onboarding_completed_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="family_booking_view",
            field=models.CharField(
                choices=[("tabs", "Una pestanya per infant"), ("matrix", "Matriu compartida")],
                default="tabs",
                max_length=12,
            ),
        ),
    ]
