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

# Link previews (deploy)

Chat apps unfurl a shared plan link (`https://prunplanner.org/shared/<uuid>`) from the Open Graph tags of that URL,
but the frontend is static files with one set of tags. `og/` serves per-plan tags and a 1200×630 card image:
`GET /og/shared/<uuid>/` (HTML) and `GET /og/shared/<uuid>.png?v=<hash>`, throttled per IP (`og`, 60/min).

The server's Traefik sends link-preview crawlers on the frontend host to the backend; people keep getting the app.
Labels on the backend service in the server compose file (`/root/PRUNplanner/docker-compose.yml`):

```yaml
- traefik.http.routers.og-preview.rule=Host(`prunplanner.org`) && PathPrefix(`/shared/`) && HeaderRegexp(`User-Agent`, `(?i)(discordbot|slackbot|twitterbot|telegrambot|facebookexternalhit|whatsapp|linkedinbot|redditbot|mastodon|bluesky|skypeuripreview|embedly)`)
- traefik.http.routers.og-preview.priority=100
- traefik.http.routers.og-preview.entrypoints=websecure
- traefik.http.routers.og-preview.tls=true
- traefik.http.routers.og-preview.service=backend
- traefik.http.routers.og-preview.middlewares=og-path,og-host
- traefik.http.middlewares.og-path.replacepathregex.regex=^/shared/([^/]+)/?$$
- traefik.http.middlewares.og-path.replacepathregex.replacement=/og/shared/$$1/
# the API host: Django's ALLOWED_HOSTS stays as it is, and og:image points at the API, where the PNG is served
- traefik.http.middlewares.og-host.headers.customrequestheaders.Host=api.prunplanner.org
```

`priority` must be above the frontend router's; `service` is the backend's Traefik service name. Check after a
deploy: `curl -A "Discordbot/2.0" https://prunplanner.org/shared/<uuid>` returns the tags, a browser user agent the
app.

# 🛠 Developer Setup

All setups read their configuration from a `.env` file in the repository root. Start from the example:

```shell
cp env.example .env
```

## Docker

`docker compose up` starts PostgreSQL, Redis, the Django backend on http://localhost:8000 (API docs at `/docs/`),
a Celery worker and Celery beat; Vector only ships logs when `VECTOR_ACTIVE=true`. The `entrypoint.sh` runs the
migrations. There won't be any game data, but the admin's "Import from FIO" actions load it.

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
    command: uv run gunicorn -c backend/gunicorn.conf.py

  # Celery Beat
  beat:
    image: ghcr.io/prunplanner/prunplanner-backend:latest
    container_name: prunplanner-beat
    command: uv run celery -A core beat --loglevel=info --scheduler core.beat:QueueDepthScheduler

  # Celery Worker
  worker:
    image: ghcr.io/prunplanner/prunplanner-backend:latest
    container_name: prunplanner-worker
    command: uv run celery -A core worker --concurrency 1 --loglevel=info
```

## Console

PRUNplanner uses [`uv`](https://docs.astral.sh/uv/) for Python 3.12 package and virtual environment management.
See [uv installation documentation](https://docs.astral.sh/uv/getting-started/installation/) on how to get started.

Run in terminal with a PostgreSQL database and Redis instance available and configured in the `.env` file.

```shell
# Install packages
uv sync

# Django
uv run backend/manage.py runserver

# Celery Worker + Beat
uv run --env-file .env celery -A core --workdir=backend worker -l INFO
uv run --env-file .env celery -A core --workdir=backend beat -l INFO  --scheduler core.beat:QueueDepthScheduler
```

## Via [`overmind`](https://github.com/DarthSim/overmind) and `Procfile`

Runs the three processes of the repository's `Procfile` (the console commands above). Packages must be installed
(`uv sync`), and the database and Redis available and defined in your `.env` file.

```shell
# start all
overmind start

# start dedicated processes
overmind start -l web,worker,beat

# to restart, send from another terminal in that folder
overmind restart
```

## Linting, Type Checks and Tests

[![Codacy Badge](https://app.codacy.com/project/badge/Grade/d1e84c37ed4048bca3c72de0253055c8)](https://app.codacy.com/gh/PRUNplanner/backend/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade) [![Codacy Badge](https://app.codacy.com/project/badge/Coverage/d1e84c37ed4048bca3c72de0253055c8)](https://app.codacy.com/gh/PRUNplanner/backend/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_coverage)

Lint & format, type checks and tests run as a GitHub Action on every pull request. Tests use SQLite locally
and PostgreSQL in CI (`TEST_DB=postgres` plus the `DATABASE_*` variables), and CI fails when coverage drops below
`fail_under` in `.coveragerc`.

```shell
# linting and formatting
uv run ruff check
uv run ruff format --check

# type checks (excluding migration files)
uv run ty check --exclude "**/migrations/*.py"

# tests
uv run pytest

# coverage (output to html)
uv run pytest --cov=. --cov-report=html
```

Benchmarks and load tests: see [`perf/README.md`](perf/README.md).
