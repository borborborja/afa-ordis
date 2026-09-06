import logging
import time

from django.core.management.base import BaseCommand
from django.contrib.sessions.models import Session
from django.db import close_old_connections
from django.utils import timezone

from apps.cafeteria.models import FamilyImportBatch
from apps.cafeteria.maintenance import PortalBusy, portal_lock
from apps.cafeteria.tasks import prepare_due_monthly_statements, send_due_daily_reports

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Executa les tasques planificades de menjador dins del contenidor únic."

    def handle(self, *args, **options):
        with portal_lock(exclusive=True, name="scheduler"):
            self.run()

    def run(self):
        self.stdout.write("Planificador de menjador iniciat.")
        cleaned_on = None
        while True:
            try:
                close_old_connections()
                with portal_lock():
                    from apps.cafeteria.privacy import restore_marker
                    if restore_marker().exists():
                        continue
                    send_due_daily_reports()
                    prepare_due_monthly_statements()
                    today = timezone.localdate()
                    if cleaned_on != today:
                        from apps.cafeteria.privacy import privacy_maintenance
                        privacy_maintenance()
                        Session.objects.filter(expire_date__lt=timezone.now()).delete()
                        FamilyImportBatch.objects.filter(
                            status=FamilyImportBatch.Status.PREVIEW, expires_at__lt=timezone.now(),
                        ).update(status=FamilyImportBatch.Status.EXPIRED, valid_rows=[], errors=[])
                        cleaned_on = today
            except PortalBusy:
                pass
            except Exception:
                logger.exception("Error en executar les tasques planificades")
            finally:
                close_old_connections()
                time.sleep(30)
