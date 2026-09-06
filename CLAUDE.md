# CLAUDE.md - PRUNplanner Backend Guidelines

## Project Summary
This repository contains the backend engine for **PRUNplanner.org** (a Prosperous Universe empire and base planning tool). It provides a stateless REST API, market data sync (CXPC/FIO), dynamic game data mapping, and background scheduling.

## Tech Stack & Architecture
- **Language**: Python 3.12
- **Framework**: Django + Django REST Framework (DRF)
- **Validation**: Pydantic / DRF Serializers
- **Package Manager**: `uv`
- **Task Queue & Scheduler**: Celery + Redis + `django_celery_beat`
- **Database**: PostgreSQL
- **Email**: Resend

---

## Development Workflow & Commands

### Package & Environment Management
- Sync dependencies: `uv sync`
- Run management commands: `uv run backend/manage.py <command>`

### Running Locally
- **Django Server**: `uv run backend/manage.py runserver`
- **Celery Worker**: `uv run --env-file .env celery -A core --workdir=backend worker -l INFO`
- **Celery Beat**: `uv run --env-file .env celery -A core --workdir=backend beat -l INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler`
- **Via Overmind**: `overmind start` (uses root `Procfile`)

### Code Quality & Testing
- **Linting & Formatting**: `uv run ruff check`
- **Type Checking**: `uv run ty check --exclude "**/migrations/*.py"`
- **Unit Tests**: `uv run pytest`
- **Coverage**: `uv run pytest --cov=. --cov-report=html`

## Testing Standards
- Write unit and integration tests using `pytest` and `pytest-django`.
- Mock external network calls (FIO API, Resend), but execute real DB operations in tests.
- Always test happy paths, validation errors, edge cases, and correct HTTP status codes.
- Use explicit type annotations in test helpers; avoid `typing.Any`.
- Use `model_bakery` (`baker.make`) and factory fixtures from `conftest.py` instead of manual `Model.objects.create()` calls.

---

## Architectural & Code Quality Principles

### 1. Architectural Impact & Maintainability
- Favor long-lasting, properly architected solutions over quick hacks.
- Keep domain logic, tasks (Celery), and API interfaces cleanly decoupled.
- Avoid single-use abstractions; enforce **Simplicity First** — write the minimum code required to solve the problem cleanly.

### 2. Strict Type Safety & Validation
- Fully typed signatures on functions and methods. **Do not use `typing.Any`**.
- Validate external payloads (FIO API responses, market sync, incoming endpoints) strictly using **Pydantic** models or DRF serializers.
- Exclude Django database `migrations/` from type checks.

### 3. Performance & Memory Considerations
- Minimize response payload sizes for public/high-frequency REST endpoints.
- Optimize database queries with `select_related` and `prefetch_related` to avoid N+1 query overhead.
- Ensure Celery tasks are idempotent and lean, offloading heavy sync operations safely without locking the database.

---

## Django & DRF Rules
- Django `manage.py` and application code reside inside the `backend/` directory; `core` serves as the base Django configuration directory.
- Always run database migrations via `uv run backend/manage.py makemigrations` and `uv run backend/manage.py migrate`.
- Maintain strict type hints for custom manager/queryset methods and DRF serializer fields.


---

## Repository & Code Layout Conventions

Every domain app (`planning`, `user`, `gamedata`, `analytics`) follows the
same internal shape; match it for any new app or module:

- `models.py`, `admin.py`, `apps.py`, `signals.py` at the app root.
- `api/` — `urls.py`, `serializers/`, `viewsets/`. Serializers validate and
  shape data; business logic belongs in `services/`, not the viewset.
- `schemas/` — Pydantic models for versioned JSON payloads stored in DB
  fields (e.g. `planning/schemas/planning_plan_data.py`). Version with a
  `_V1`, `_V2`, ... suffix and register the current one in that app's
  `latest_schemas.py` (`LATEST_SCHEMA` dict) — never mutate a shipped schema
  in place.
- `services/` — business logic decoupled from the API/task layer.
- `<app>_cache_manager.py` — a subclass of `core.services.cache_manager.CacheManager`
  for apps that cache response payloads, with `key_for_*`/`key_*` classmethods
  building cache keys and a `get_or_set_response` per endpoint.
- `signals.py` — `post_save`/`post_delete` receivers that invalidate cache
  keys via `transaction.on_commit(...)`, each with an explicit `dispatch_uid`.
- `migrations/` — generated only, via `makemigrations`; never hand-edited.

Tests mirror this exactly under `backend/tests/<app>/`, path-for-path
(`planning/api/viewsets/plan_viewset.py` →
`tests/planning/api/viewsets/test_plan_viewset.py`), with an app-local
`conftest.py` for fixtures that don't belong in the root one.

## Definition of Done

Before considering a change complete:

- `uv run ruff check` and `uv run ruff format --check` pass.
- `uv run ty check --exclude "**/migrations/*.py"` passes.
- `uv run pytest` passes, with new tests for new behavior
- No hand-edited files under any `migrations/` directory.
- No `typing.Any` introduced; no N+1 queries in list/detail endpoints.
