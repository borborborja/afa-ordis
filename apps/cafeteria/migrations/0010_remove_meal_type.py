# Removes the former special meal type. Existing reservations and statement
# lines retain their diet name, price and billing data.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("cafeteria", "0009_localize_profile_language"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="mealbooking",
            name="meal_type",
        ),
        migrations.RemoveField(
            model_name="teachermealbooking",
            name="meal_type",
        ),
        migrations.RemoveField(
            model_name="statementline",
            name="meal_type",
        ),
        migrations.RemoveField(
            model_name="teacherstatementline",
            name="meal_type",
        ),
    ]
