"""
Task health: what beat's `last_run_at` can't tell (it's when beat dispatched, and results are ignored).

Celery signal receivers write a Redis hash per task name (`taskhealth:<name>`: last success/failure, last error,
last runtime) and daily ok/fail counters (`taskhealth:<name>:<YYYYMMDD>:ok|fail`, 15-day TTL) for the tracker.
Receivers never raise into the task. `task_health_rows` joins that with each `PeriodicTask` and its schedule.
"""

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, cast

import structlog
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from redis import Redis

if TYPE_CHECKING:
    from django_celery_beat.models import PeriodicTask

logger = structlog.get_logger(__name__)

PREFIX = 'taskhealth'
NAMES_KEY = f'{PREFIX}:names'
DAY_TTL_SECONDS = int(timedelta(days=15).total_seconds())
TRACKER_DAYS = 14
ERROR_MAX_CHARS = 500

STATES = ('paused', 'ok', 'overdue', 'failing')

# task id -> monotonic start, filled by task_prerun and emptied by task_postrun in the same worker process
_started: dict[str, float] = {}


def _redis() -> Redis:
    from django_redis import get_redis_connection

    return get_redis_connection('default')


def hash_key(name: str) -> str:
    return f'{PREFIX}:{name}'


def day_key(name: str, day: date, outcome: str) -> str:
    return f'{PREFIX}:{name}:{day:%Y%m%d}:{outcome}'


def record(name: str, *, ok: bool, error: str | None = None, runtime_ms: int | None = None) -> None:
    try:
        now = timezone.now()
        fields: dict[str, str | int] = {'last_success_at' if ok else 'last_failure_at': now.isoformat()}
        if not ok:
            fields['last_error'] = (error or '')[:ERROR_MAX_CHARS]
        if runtime_ms is not None:
            fields['last_runtime_ms'] = runtime_ms

        counter = day_key(name, now.date(), 'ok' if ok else 'fail')
        pipe = _redis().pipeline()
        pipe.hset(hash_key(name), mapping=fields)
        pipe.sadd(NAMES_KEY, name)
        pipe.incr(counter)
        pipe.expire(counter, DAY_TTL_SECONDS)
        pipe.execute()
    except Exception:
        logger.exception('task_health_record_failed', task=name)


def on_task_prerun(task_id: str | None = None, **kwargs: object) -> None:
    if task_id:
        _started[task_id] = time.monotonic()


def on_task_postrun(
    task_id: str | None = None, task: object = None, retval: object = None, state: str | None = None, **kwargs: object
) -> None:
    """SUCCESS and FAILURE are recorded; RETRY and other states only release the start time."""
    try:
        started = _started.pop(task_id, None) if task_id else None
        name = getattr(task, 'name', None)
        if not name or state not in ('SUCCESS', 'FAILURE'):
            return
        runtime_ms = None if started is None else int((time.monotonic() - started) * 1000)
        record(name, ok=state == 'SUCCESS', error=None if state == 'SUCCESS' else repr(retval), runtime_ms=runtime_ms)
    except Exception:
        logger.exception('task_health_receiver_failed')


@dataclass
class DayCell:
    day: date
    ok: int
    fail: int


@dataclass
class TaskHealth:
    name: str
    task: str
    periodic_task_id: int | None = None
    enabled: bool = True
    schedule: str = ''
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str = ''
    last_runtime_ms: int | None = None
    overdue: bool = False
    days: list[DayCell] = field(default_factory=list)

    @property
    def failing(self) -> bool:
        if self.last_failure_at is None:
            return False
        return self.last_success_at is None or self.last_failure_at > self.last_success_at

    @property
    def state(self) -> str:
        if not self.enabled:
            return 'paused'
        if self.overdue:
            return 'overdue'
        if self.failing:
            return 'failing'
        return 'ok'


def is_overdue(periodic_task: 'PeriodicTask', now: datetime) -> bool:
    """
    Overdue when the run after the missed one is due too, i.e. more than one interval late. Works for interval and
    crontab schedules alike, since both answer `remaining_estimate`; anything else is never overdue.
    """
    if not periodic_task.enabled:
        return False
    try:
        schedule = periodic_task.schedule
        reference = periodic_task.last_run_at or periodic_task.date_changed
        due = now + schedule.remaining_estimate(reference)
        if due >= now:
            return False
        return schedule.remaining_estimate(due) < timedelta(0)
    except Exception:
        logger.exception('task_health_schedule_failed', task=periodic_task.name)
        return False


def _decode(value: bytes | str | None) -> str | None:
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else value


def _read_redis(extra_names: bool, known: set[str], today: date) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Hash per task name plus the names seen only in Redis (non-periodic tasks)."""
    r = _redis()
    names = set(known)
    others: list[str] = []
    if extra_names:
        members = cast(set[bytes], r.smembers(NAMES_KEY))
        others = sorted({n for raw in members if (n := _decode(raw))} - known)
        names |= set(others)

    ordered = sorted(names)
    days = [today - timedelta(days=offset) for offset in range(TRACKER_DAYS - 1, -1, -1)]
    pipe = r.pipeline()
    for name in ordered:
        pipe.hgetall(hash_key(name))
        pipe.mget([day_key(name, day, outcome) for day in days for outcome in ('ok', 'fail')])
    results = pipe.execute()

    data: dict[str, dict[str, str]] = {}
    for index, name in enumerate(ordered):
        raw_hash, raw_days = results[2 * index], results[2 * index + 1]
        entry = {str(_decode(k)): str(_decode(v)) for k, v in raw_hash.items()}
        for position, day in enumerate(days):
            entry[f'ok:{day}'] = str(_decode(raw_days[2 * position]) or 0)
            entry[f'fail:{day}'] = str(_decode(raw_days[2 * position + 1]) or 0)
        data[name] = entry
    return data, others


def _apply(row: TaskHealth, entry: dict[str, str], today: date) -> None:
    success, failure = entry.get('last_success_at'), entry.get('last_failure_at')
    row.last_success_at = parse_datetime(success) if success else None
    row.last_failure_at = parse_datetime(failure) if failure else None
    row.last_error = entry.get('last_error', '')
    runtime = entry.get('last_runtime_ms')
    row.last_runtime_ms = int(runtime) if runtime else None
    row.days = [
        DayCell(day=day, ok=int(entry.get(f'ok:{day}', 0)), fail=int(entry.get(f'fail:{day}', 0)))
        for day in (today - timedelta(days=offset) for offset in range(TRACKER_DAYS - 1, -1, -1))
    ]


def task_health_rows(state: str | None = None, include_unscheduled: bool = True) -> tuple[list[TaskHealth], bool]:
    """
    One row per periodic task, plus task names only seen in Redis. Returns the rows and whether Redis answered;
    without Redis the rows still carry schedule and overdue state. `state='overdue'` keeps exactly the rows the
    dashboard's overdue chip counts.
    """
    from django_celery_beat.models import PeriodicTask

    now = timezone.now()
    today = now.date()
    periodic = list(PeriodicTask.objects.select_related('interval', 'crontab', 'solar', 'clocked').order_by('name'))
    rows = [
        TaskHealth(
            name=pt.name,
            task=pt.task,
            periodic_task_id=pt.pk,
            enabled=pt.enabled,
            schedule=str(pt.scheduler or ''),
            last_run_at=pt.last_run_at,
            overdue=is_overdue(pt, now),
        )
        for pt in periodic
    ]

    redis_ok = True
    try:
        data, others = _read_redis(include_unscheduled, {row.task for row in rows}, today)
        rows += [TaskHealth(name=name, task=name) for name in others]
        for row in rows:
            _apply(row, data.get(row.task, {}), today)
    except Exception:
        logger.exception('task_health_redis_failed')
        redis_ok = False

    if state == 'overdue':
        rows = [row for row in rows if row.overdue]
    elif state:
        rows = [row for row in rows if row.state == state]
    return rows, redis_ok
