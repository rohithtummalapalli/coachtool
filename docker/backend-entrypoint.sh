#!/usr/bin/env sh
set -eu

cd /app/backend

python manage.py migrate --noinput

exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:${DJANGO_PORT:-8001} \
  --workers ${GUNICORN_WORKERS:-2} \
  --threads ${GUNICORN_THREADS:-2} \
  --timeout ${GUNICORN_TIMEOUT:-60}
