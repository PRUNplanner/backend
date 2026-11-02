# PRUNplanner backend

REST API for [PRUNplanner.org](https://prunplanner.org): Django + DRF, Python 3.12, `uv`, PostgreSQL,
Redis (cache and broker), Celery + `django_celery_beat`. Setup and running: `README.md`; load tests:
`perf/README.md`. When this file and the code disagree, the code is right; fix this file.

## Layout

Django lives in `backend/` (`manage.py`, settings in `core/config/`). Domain apps: `planning`, `user`,
`gamedata`, `analytics`; shared code in `core`, URL root and API helpers in `api`.

- `api/` per app: `urls.py`, `serializers/`, `viewsets/`. Viewsets stay thin; business logic goes in
  `services/`, Celery tasks in `tasks.py`.
- Versioned JSON stored in DB fields is a Pydantic model in `planning/schemas/` with a `_V1`, `_V2`, ... suffix,
  registered in `latest_schemas.py`. Never change a shipped version in place.
- Response cache: `core/services/cache_manager.py`. Each app declares its `CacheNamespace`s in one
  `*_cache_manager.py`. Viewsets serve through `CacheManager.respond(...)`; private namespaces pass the user id
  as `scope`. Invalidate only by version bump (`CacheManager.invalidate` / `invalidate_on_commit`, called from
  `signals.py` receivers with an explicit `dispatch_uid`); never delete keys or scan patterns.
- `migrations/` are generated with `makemigrations`, never hand-edited.
- Tests mirror the code path for path under `backend/tests/<app>/` (`planning/api/viewsets/plan_viewset.py` →
  `tests/planning/api/viewsets/test_plan_viewset.py`).

## Rules

- Full type hints, no `typing.Any`. Validate external payloads (FIO, webhooks, request bodies) with Pydantic or
  DRF serializers.
- No N+1 queries: `select_related` / `prefetch_related` on list and detail endpoints. Keep public payloads small.
- Celery tasks are idempotent.
- Tests: `pytest` + `pytest-django`, real DB, mocked network (FIO, Resend). Build objects with `model_bakery`
  and the `conftest.py` fixtures. Cover happy path, validation errors, edge cases and status codes.
- Tests run on SQLite locally and on Postgres in CI (`TEST_DB=postgres` + `DATABASE_*`); Postgres-only SQL
  tests skip on SQLite.
- The API's consumer is [PRUNplanner/frontend](https://github.com/PRUNplanner/frontend), which validates every
  response against Zod schemas (`src/features/api/schemas/`). Renaming, removing, retyping or nulling a field
  breaks it; adding fields does not. Decimals are JSON numbers.

## Logging

structlog JSON lines on stdout (`core/config/settings/logging.py`), shipped by Vector to Axiom dashboards.
Event and field names are what those dashboards query: don't rename them casually, and note any rename in the PR.

- `logger = structlog.get_logger(__name__)`; the event is snake_case `<noun>_<past verb>`
  (`fio_refresh_failed`), details as keyword fields. Reuse keys: `user_id`, `duration_ms`, `ticker`,
  `exchange_code`, `planet_natural_id`.
- Already bound, don't log them yourself: request context (`request_id`, `user_id`, `ip`, `route`,
  `duration_ms`, `auth`), task context (`task`, `task_id`, `queue_ms`), and `db_queries` / `db_ms`
  (`core/services/db_stats.py`).
- `info` for business events, `warning` for expected failures (bad user FIO key, FIO error status or timeout),
  `logger.exception` in `except` for what needs a look. Log a failure once, where it's handled.
- One line when done (with `duration_ms`), no start/finish pairs.
- Never log secrets, emails, API keys, tokens or submitted form values. No unbounded dicts: every key becomes
  an Axiom column.

## Definition of Done

- `uv run ruff check` and `uv run ruff format --check` pass.
- `uv run ty check --exclude "**/migrations/*.py"` passes.
- `uv run pytest` passes, with tests for new behavior; coverage stays above `fail_under` in `.coveragerc`.
- Model changes come with a generated migration.
- An intended API change refreshes the schema snapshot:
  `UPDATE_OPENAPI_SNAPSHOT=1 uv run pytest backend/tests/api/test_openapi_schema.py`.
