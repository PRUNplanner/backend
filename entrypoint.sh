#!/bin/bash
set -e

# Waiting for Database
if [ "$DATABASE_HOST" ]; then
    echo "Waiting for $DATABASE_HOST:$DATABASE_PORT..."
    while ! nc -z "$DATABASE_HOST" "$DATABASE_PORT"; do
      sleep 0.5
    done
    echo "DB is ready."
fi

# Migrations & Static files
if [ "$SERVICE_TYPE" = "django" ]; then
    echo "Running Migrations..."
    uv run python backend/manage.py migrate --noinput

    echo "Collecting Static..."
    uv run python backend/manage.py collectstatic --noinput --clear
fi

# Exec container command
exec "$@"
