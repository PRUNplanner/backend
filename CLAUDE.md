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
- **Celery Beat**: `uv run --env-file .env celery -A core --workdir=backend beat -l INFO --scheduler core.beat:QueueDepthScheduler`
- **Via Overmind**: `overmind start` (uses root `Procfile`)

### Code Quality & Testing
- **Linting & Formatting**: `uv run ruff check`
- **Type Checking**: `uv run ty check --exclude "**/migrations/*.py"`
- **Unit Tests**: `uv run pytest`
- **Coverage**: `uv run pytest --cov=. --cov-report=html`
- **Performance / load**: `perf/run.sh` (needs Docker; see `perf/README.md`)

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
- `<app>_cache_manager.py` — only the app's `CacheNamespace` constants
  (name, TTL, `private`). Viewsets cache with
  `CacheManager.respond(request, NS, '<endpoint>', *parts, build=..., scope=...)`;
  private namespaces pass the user id as `scope`. Keys hash the parts, so
  request input never lands in a key. Invalidate only with
  `CacheManager.invalidate(NS, scope)` (a version bump); never delete keys or
  scan patterns. Every cache entry has a TTL; version counters must not.
- `signals.py` — `post_save`/`post_delete` receivers that call
  `CacheManager.invalidate_on_commit(NS, scope)`, each with an explicit
  `dispatch_uid`.
- `migrations/` — generated only, via `makemigrations`; never hand-edited.

Tests mirror this exactly under `backend/tests/<app>/`, path-for-path
(`planning/api/viewsets/plan_viewset.py` →
`tests/planning/api/viewsets/test_plan_viewset.py`), with an app-local
`conftest.py` for fixtures that don't belong in the root one.

## Logging

Logs are JSON lines on stdout (structlog, `core/config/settings/logging.py`),
shipped by Vector (`vector.toml`) to Axiom, where they feed dashboards.

- `logger = structlog.get_logger(__name__)`; log an event name, not a
  sentence: snake_case `<noun>_<past verb>` (`fio_refresh_failed`,
  `planet_search_completed`), details as keyword fields.
- Standard keys: `user_id`, `duration_ms`, `ticker`, `exchange_code`,
  `planet_natural_id`. Requests already carry `request_id`, `user_id`, `ip`,
  `route`, `duration_ms`; tasks carry `task`, `task_id` and the queuing
  request's `request_id`. Don't bind these again.
- Added for you, don't log them yourself: `db_queries` / `db_ms` (query count
  and total query time, `core/services/db_stats.py`) on `request_finished`,
  `task_succeeded` and `task_failed`; `queue_ms` (publish to start, including
  time held back by a rate limit or eta) on every line of a task, so read it
  from `task_started`. A task queued without the publish-time header has no
  `queue_ms`.
- `celery_queue_depth` (`depth`, `depth_high` / `depth_normal` / `depth_low`
  for priority 0-3 / 4-6 / 7-10) is logged about once a minute by the beat
  scheduler (`core/beat.py`), `celery_queue_depth_failed` once per Redis
  outage. Beat has to run with `--scheduler core.beat:QueueDepthScheduler`.
- Refresh failures: `fio_refresh_failed`, `planet_refresh_failed`,
  `planet_infrastructure_refresh_failed`, `cxpc_refresh_failed`,
  `exchanges_refresh_failed`. A FIO error status (with `status_code`) or
  transport error (timeout, dropped connection) is a warning, anything else
  `logger.exception`.
- Levels: `info` for a business event, `warning` for an expected failure
  (bad user FIO key, invalid webhook payload), `logger.exception` inside
  `except` for what needs a look. Log a failure once, where it's handled.
- No start/finish pairs: one line when done, with `duration_ms`.
- No secrets, emails, API keys or tokens (the webhook path token is redacted
  by a processor). No unbounded dicts: every key becomes an Axiom column.
- `fio_request_completed` / `fio_request_failed` fields (`endpoint`, `url`,
  `status_code`, `duration` in seconds, `bytes`) back Axiom dashboards;
  don't rename them.
- The Axiom dashboards are built in the workspace repo
  (`../axiom/build_dashboards.py`). Renaming an event or field it queries
  breaks a panel; update the builder in the same change.

## Definition of Done

Before considering a change complete:

- `uv run ruff check` and `uv run ruff format --check` pass.
- `uv run ty check --exclude "**/migrations/*.py"` passes.
- `uv run pytest` passes, with new tests for new behavior
- No hand-edited files under any `migrations/` directory.
- No `typing.Any` introduced; no N+1 queries in list/detail endpoints.
