#!/bin/sh
set -eu

if [ "${SKIP_INITIALIZATION:-false}" != "true" ]; then
  python manage.py migrate --noinput
  python manage.py collectstatic --noinput

  if [ "${BOOTSTRAP_SUPERUSER:-true}" = "true" ]; then
    python manage.py bootstrap_superuser
  fi
fi

exec "$@"
