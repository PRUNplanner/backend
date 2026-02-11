web: uv run backend/manage.py runserver
worker: uv run --env-file .env celery -A core --workdir=backend worker -l INFO
beat: uv run --env-file .env celery -A core --workdir=backend beat -l INFO  --scheduler django_celery_beat.schedulers:DatabaseScheduler

web_vector: uv run gunicorn --pythonpath backend core.wsgi:application --bind 0.0.0.0:8000 --preload --workers 3 --threads 2 2>&1 | vector --config vector-local.toml
worker_vector: uv run celery -A core --workdir=backend worker --concurrency 1 -l INFO 2>&1 | vector --config vector-local.toml
beat_vector: uv run celery -A core --workdir=backend beat -l INFO  --scheduler django_celery_beat.schedulers:DatabaseScheduler 2>&1 | vector --config vector-local.toml


