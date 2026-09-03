#!/bin/sh
set -eu

python manage.py migrate --noinput
python manage.py collectstatic --noinput

if [ "${BOOTSTRAP_SUPERUSER:-true}" = "true" ]; then
  python manage.py bootstrap_superuser
fi

# SQLite is used by a single application instance. The scheduler runs in the
# same container to avoid Redis, Celery workers and a separate beat sidecar.
if [ "${RUN_SCHEDULER:-true}" = "true" ]; then
  python manage.py run_scheduler &
fi

exec "$@"
