web: uv run backend/manage.py runserver
worker: SERVICE_TYPE=celery uv run --env-file .env celery -A core --workdir=backend worker -l INFO
beat: SERVICE_TYPE=celery uv run --env-file .env celery -A core --workdir=backend beat -l INFO  --scheduler core.beat:QueueDepthScheduler
