from collections.abc import Callable, Iterator
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from core.services import task_health
from core.services.task_health import (
    NAMES_KEY,
    TaskHealth,
    day_key,
    hash_key,
    is_overdue,
    on_task_postrun,
    on_task_prerun,
    task_health_rows,
)
from django.utils import timezone
from django_celery_beat.models import IntervalSchedule, PeriodicTask
from model_bakery import baker


class FakeRedis:
    """Just enough Redis for task health: hashes, one set, counters, and a pipeline that runs them in order."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, bytes]] = {}
        self.sets: dict[str, set[bytes]] = {}
        self.counters: dict[str, int] = {}

    def pipeline(self) -> 'FakePipeline':
        return FakePipeline(self)

    def smembers(self, key: str) -> set[bytes]:
        return self.sets.get(key, set())


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.calls: list[Callable[[], object]] = []

    def hset(self, key: str, mapping: dict[str, str | int]) -> None:
        self.calls.append(
            lambda: self.redis.hashes.setdefault(key, {}).update({k: str(v).encode() for k, v in mapping.items()})
        )

    def sadd(self, key: str, value: str) -> None:
        self.calls.append(lambda: self.redis.sets.setdefault(key, set()).add(value.encode()))

    def incr(self, key: str) -> None:
        def run() -> int:
            self.redis.counters[key] = self.redis.counters.get(key, 0) + 1
            return self.redis.counters[key]

        self.calls.append(run)

    def expire(self, key: str, seconds: int) -> None:
        self.calls.append(lambda: True)

    def hgetall(self, key: str) -> None:
        self.calls.append(lambda: self.redis.hashes.get(key, {}))

    def mget(self, keys: list[str]) -> None:
        self.calls.append(
            lambda: [str(self.redis.counters[k]).encode() if k in self.redis.counters else None for k in keys]
        )

    def execute(self) -> list[object]:
        results = [call() for call in self.calls]
        self.calls = []
        return results


@pytest.fixture
def fake_redis() -> Iterator[FakeRedis]:
    redis = FakeRedis()
    with patch.object(task_health, '_redis', return_value=redis):
        yield redis


def run_task(name: str, state: str, retval: object = None) -> None:
    task = MagicMock()
    task.name = name
    on_task_prerun(task_id='t1', task=task)
    on_task_postrun(task_id='t1', task=task, retval=retval, state=state)


class TestReceivers:
    """AC15: success and failure each update taskhealth:<name>; a Redis error never fails the task."""

    def test_success_records_hash_counter_and_name(self, fake_redis: FakeRedis) -> None:
        run_task('update_daily_stats', 'SUCCESS')

        entry = fake_redis.hashes[hash_key('update_daily_stats')]
        assert 'last_success_at' in entry
        assert 'last_runtime_ms' in entry
        assert fake_redis.counters[day_key('update_daily_stats', timezone.now().date(), 'ok')] == 1
        assert b'update_daily_stats' in fake_redis.smembers(NAMES_KEY)

    def test_failure_records_the_error(self, fake_redis: FakeRedis) -> None:
        run_task('update_daily_stats', 'FAILURE', retval=ValueError('x' * 800))

        entry = fake_redis.hashes[hash_key('update_daily_stats')]
        assert 'last_failure_at' in entry
        assert entry['last_error'].startswith(b"ValueError('xxx")
        assert len(entry['last_error']) == task_health.ERROR_MAX_CHARS
        assert fake_redis.counters[day_key('update_daily_stats', timezone.now().date(), 'fail')] == 1

    def test_retry_records_nothing(self, fake_redis: FakeRedis) -> None:
        run_task('update_daily_stats', 'RETRY')

        assert (fake_redis.hashes, fake_redis.sets, fake_redis.counters) == ({}, {}, {})
        assert task_health._started == {}

    def test_redis_error_does_not_raise(self) -> None:
        with patch.object(task_health, '_redis', side_effect=ConnectionError('down')):
            run_task('update_daily_stats', 'SUCCESS')  # no exception

    def test_receivers_are_connected(self) -> None:
        from celery.signals import task_postrun, task_prerun

        assert any('task_health_postrun' in str(receiver[0]) for receiver in task_postrun.receivers or [])
        assert any('task_health_prerun' in str(receiver[0]) for receiver in task_prerun.receivers or [])


@pytest.mark.django_db
class TestOverdue:
    """AC16: overdue past the schedule plus one interval."""

    @pytest.fixture
    def hourly(self) -> IntervalSchedule:
        return baker.make(IntervalSchedule, every=1, period=IntervalSchedule.HOURS)

    @pytest.mark.parametrize(
        'last_run_minutes_ago, expected',
        [
            (30, False),  # not due yet
            (90, False),  # due 30 min ago: within one interval of grace
            (150, True),  # the run after the missed one is due too
        ],
    )
    def test_interval_schedule(self, hourly: IntervalSchedule, last_run_minutes_ago: int, expected: bool) -> None:
        now = timezone.now()
        task = baker.make(
            PeriodicTask, interval=hourly, enabled=True, last_run_at=now - timedelta(minutes=last_run_minutes_ago)
        )

        assert is_overdue(task, now) is expected

    def test_paused_is_never_overdue(self, hourly: IntervalSchedule) -> None:
        now = timezone.now()
        task = baker.make(PeriodicTask, interval=hourly, enabled=False, last_run_at=now - timedelta(days=3))

        assert is_overdue(task, now) is False

    def test_rows_filter_overdue(self, hourly: IntervalSchedule, fake_redis: FakeRedis) -> None:
        now = timezone.now()
        baker.make(PeriodicTask, name='late', task='late', interval=hourly, last_run_at=now - timedelta(hours=5))
        baker.make(PeriodicTask, name='fine', task='fine', interval=hourly, last_run_at=now - timedelta(minutes=5))

        rows, redis_ok = task_health_rows(state='overdue')

        assert redis_ok is True
        assert [row.name for row in rows] == ['late']
        assert rows[0].state == 'overdue'


@pytest.mark.django_db
class TestStates:
    def test_failing_when_last_failure_is_newer(self) -> None:
        now = timezone.now()
        row = TaskHealth(name='x', task='x', last_success_at=now - timedelta(hours=1), last_failure_at=now)

        assert row.failing and row.state == 'failing'

    def test_ok_when_last_success_is_newer(self) -> None:
        now = timezone.now()
        row = TaskHealth(name='x', task='x', last_success_at=now, last_failure_at=now - timedelta(hours=1))

        assert not row.failing and row.state == 'ok'

    def test_paused_wins(self) -> None:
        assert TaskHealth(name='x', task='x', enabled=False, overdue=True).state == 'paused'

    def test_rows_join_redis_data_and_unscheduled_names(self, fake_redis: FakeRedis) -> None:
        interval = baker.make(IntervalSchedule, every=1, period=IntervalSchedule.HOURS)
        baker.make(PeriodicTask, name='Daily stats', task='update_daily_stats', interval=interval)
        run_task('update_daily_stats', 'SUCCESS')
        run_task('update_daily_stats', 'FAILURE', retval=RuntimeError('boom'))
        run_task('gamedata_refresh_user_fiodata', 'SUCCESS')

        rows, _ = task_health_rows()
        by_task = {row.task: row for row in rows}

        scheduled = by_task['update_daily_stats']
        assert scheduled.periodic_task_id is not None
        assert scheduled.state == 'failing'
        assert scheduled.last_error == "RuntimeError('boom')"
        assert (scheduled.days[-1].ok, scheduled.days[-1].fail) == (1, 1)
        assert len(scheduled.days) == task_health.TRACKER_DAYS
        assert by_task['gamedata_refresh_user_fiodata'].periodic_task_id is None

    def test_rows_without_redis(self) -> None:
        interval = baker.make(IntervalSchedule, every=1, period=IntervalSchedule.HOURS)
        baker.make(PeriodicTask, name='Daily stats', task='update_daily_stats', interval=interval)

        with patch.object(task_health, '_redis', side_effect=ConnectionError('down')):
            rows, redis_ok = task_health_rows()

        assert redis_ok is False
        assert [row.task for row in rows] == ['update_daily_stats']
