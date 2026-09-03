import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.cafeteria.models import ensure_role_groups


class Command(BaseCommand):
    help = "Crea el superusuari inicial a partir de SUPERUSER_EMAIL i SUPERUSER_PASSWORD."

    def handle(self, *args, **options):
        ensure_role_groups()
        user_model = get_user_model()
        if user_model.objects.exists():
            self.stdout.write("Ja hi ha usuaris; no s'ha modificat cap compte inicial.")
            return

        email = os.getenv("SUPERUSER_EMAIL", "").strip().lower()
        password = os.getenv("SUPERUSER_PASSWORD", "")
        name = os.getenv("SUPERUSER_NAME", "").strip()
        if not email or not password or email.endswith("@example.com"):
            raise CommandError("Configura SUPERUSER_EMAIL i SUPERUSER_PASSWORD reals al fitxer .env.")

        first_name, _, last_name = name.partition(" ")
        user_model.objects.create_superuser(
            username=email,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
        )
        self.stdout.write(self.style.SUCCESS(f"S'ha creat el superusuari {email}."))
