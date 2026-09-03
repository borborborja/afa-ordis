import logging
import time

from django.core.management.base import BaseCommand

from apps.cafeteria.tasks import prepare_due_monthly_statements, send_due_daily_reports

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Executa les tasques planificades de menjador dins del contenidor únic."

    def handle(self, *args, **options):
        self.stdout.write("Planificador de menjador iniciat.")
        while True:
            try:
                send_due_daily_reports()
                prepare_due_monthly_statements()
            except Exception:
                logger.exception("Error en executar les tasques planificades")
            time.sleep(30)
