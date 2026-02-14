web: uv run backend/manage.py runserver
worker: uv run --env-file .env celery -A core --workdir=backend worker -l INFO
beat: uv run --env-file .env celery -A core --workdir=backend beat -l INFO  --scheduler django_celery_beat.schedulers:DatabaseScheduler
