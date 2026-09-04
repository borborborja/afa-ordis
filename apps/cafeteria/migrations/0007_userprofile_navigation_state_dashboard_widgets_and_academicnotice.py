from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("cafeteria", "0006_academicintensiveperiod"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="dashboard_widgets",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="navigation_state",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name="AcademicNotice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=160)),
                ("description", models.TextField(blank=True)),
                ("level", models.CharField(choices=[("information", "Informació"), ("alert", "Alerta")], default="information", max_length=16)),
                ("starts_on", models.DateField()),
                ("ends_on", models.DateField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("academic_year", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notices", to="cafeteria.academicyear")),
            ],
            options={"ordering": ["starts_on", "ends_on", "title"]},
        ),
    ]
