#!/bin/sh
# Перед запуском: таблицы базы и статика Django (админка, Swagger) для nginx
set -e

mkdir -p "$(dirname "${DJANGO_DB_PATH:-/app/data/db.sqlite3}")" "${DJANGO_MEDIA_ROOT:-/app/media}"
python manage.py migrate --noinput
python manage.py collectstatic --noinput --verbosity 0

exec "$@"
