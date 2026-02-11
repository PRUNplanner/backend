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

to be created.

## Running locally

PRUNplanner uses [`uv`](https://docs.astral.sh/uv/) for Python `3.12` package and virtual environment management. See [uv installation documentation](https://docs.astral.sh/uv/getting-started/installation/) on how to get started.

Run in terminal if you have PostgreSQL database and Redis instance available and configured in the `.env` file.

```shell
# Install packages
uv sync

# Django
uv run backend/manage.py runserver

# Celery Worker + Beat
uv run --env-file .env celery -A core --workdir=backend worker -l INFO
uv run --env-file .env celery -A core --workdir=backend beat -l INFO  --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

## Running locally via [`overmind`](https://github.com/DarthSim/overmind) and `Procfile`

Packages must be installed and available as `overmind` executes multiple `uv run` command in separate processes. Create yourself a `Procfile` and use the following commands.

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
