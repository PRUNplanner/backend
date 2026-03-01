<div align="center">
  <h1>PRUNplanner Backend</h1>
</div>
<div align="center">
  <h3>The engine behind <a href="https://prunplanner.org" target="_blank">PRUNplanner.org</a>, the comprehensive empire and base planning tool.</h3>
</div>

---

While the [Frontend](https://github.com/PRUNplanner/frontend) handles all your planning, this backend is the
"brains of the operation". It provides a robust REST API for orchestrating all your planning, empire building and market data needs.


# 🚀 Features

- **Comprehensive Game-Data Mapping**: Deep integration with FIO providing an accurate database of all in-game planets, materials, recipes, and buildings for high-fidelity planning.
- **Market Intelligence:** CXPC data synchronization with automated VWAP and trading datapoint calculations for pinpoint pricing accuracy across all operations.
- **Persistent Empire & Plan Storage:** Robust backend for managing complex multi-plan configurations and user-specific CX preferences.
- **Unified Auth & User Management:** Secure handling of user profiles and preferences via a multi-tier authentication system supporting BasicAuth, JWT, and persistent API Tokens.
- **Asynchronous Data Synchronization:** High-performance task workers that continuously fetch and update site, warehouse, ship, and storage data to mirror live in-game states.
- **High-Efficiency REST API:** Clean, stateless endpoints featuring minimized payload serialization to ensure lightning-fast response times and low bandwidth overhead.

# Stack

- Python 3.12
- Django with Django Rest Framework
- Celery for task orchestration
- Redis as cache and Celery backend
- PostgreSQL database
- Resend to handle communications via email

# 🛠 Developer Setup

## Docker

You can run the backend locally with Docker / Docker Compose with the `entrypoint.sh` ensuring migrations did run.
This will start a PostgreSQL database, Redis, the Django backend as well as a Celery worker. There won't be any
game data, but the admin interface allows to import it easily.

To create yourself a local superuser for Django, run the following command:

```shell
docker compose exec backend uv run backend/manage.py createsuperuser
```

## Docker Packages

PRUNplanner provides pre-built Docker images for **AMD64** and **ARM64** (Apple Silicon/Ampere) based on the latest releases. You can find them here: [PRUNplanner Backend Packages](https://github.com/PRUNplanner/backend/pkgs/container/prunplanner-backend).

You can use the same image to run the Django backend, Celery worker, and Celery beat by overriding the `command` in your `docker-compose.yml`. Please make sure to pass your .env file or environment variables directly to the services.

```yaml
services:
  # Django + DRF Backend
  backend:
    image: ghcr.io/prunplanner/prunplanner-backend:latest
    container_name: prunplanner-backend
    command: uv run gunicorn --pythonpath backend core.wsgi:application --bind 0.0.0.0:8000 --preload --workers 3 --threads 2 --access-logfile - --error-logfile -

  # Celery Beat
  beat:
    image: ghcr.io/prunplanner/prunplanner-backend:latest
    container_name: prunplanner-beat
    command: uv run celery -A core beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler

  # Celery Worker
  worker:
    image: ghcr.io/prunplanner/prunplanner-backend:latest
    container_name: prunplanner-worker
    command: uv run celery -A core worker --concurrency 1 --loglevel=info
```

## Console

PRUNplanner uses [`uv`](https://docs.astral.sh/uv/) for Python 3.12 package and virtual environment management.
See [uv installation documentation](https://docs.astral.sh/uv/getting-started/installation/) on how to get started.

Run in terminal if you have PostgreSQL database and Redis instance available and configured in the `.env` file or
start them separately and point the Django towards it.

```shell
# Install packages
uv sync

# Django
uv run backend/manage.py runserver

# Celery Worker + Beat
uv run --env-file .env celery -A core --workdir=backend worker -l INFO
uv run --env-file .env celery -A core --workdir=backend beat -l INFO  --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

## Via [`overmind`](https://github.com/DarthSim/overmind) and `Procfile`

Packages must be installed and available as `overmind` executes multiple `uv run` command in separate processes.
Create yourself a `Procfile` and use the following commands. Database and Redis must be available and defined in your
`.env` file.

```shell
web: uv run backend/manage.py runserver
worker: uv run --env-file .env celery -A core --workdir=backend worker -l INFO
beat: uv run --env-file .env celery -A core --workdir=backend beat -l INFO  --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

```shell
# start all
overmind start

# start dedicated processes
overmind start -l web,worker,beat

# to restart, send from another terminal in that folder
overmind restart
```
