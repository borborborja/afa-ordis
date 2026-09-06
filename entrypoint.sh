#!/bin/bash
set -euo pipefail

python manage.py check --deploy --fail-level WARNING
python manage.py migrate --noinput

if [ "${BOOTSTRAP_SUPERUSER:-true}" = "true" ]; then
  python manage.py bootstrap_superuser
fi

# SQLite is used by a single application instance. The scheduler runs in the
# same container to avoid Redis, Celery workers and a separate beat sidecar.
if [ "${RUN_SCHEDULER:-true}" = "true" ]; then
  python manage.py run_scheduler &
  scheduler_pid=$!
  "$@" &
  web_pid=$!
  shutdown() {
    kill -TERM "$web_pid" "$scheduler_pid" 2>/dev/null || true
    wait "$web_pid" "$scheduler_pid" 2>/dev/null || true
  }
  trap shutdown TERM INT EXIT
  # Restart the container if either critical process exits unexpectedly.
  wait -n "$web_pid" "$scheduler_pid" || true
  exit 1
fi

exec "$@"
