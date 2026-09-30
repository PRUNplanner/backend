from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from core.beat import SAMPLE_INTERVAL, QueueDepthScheduler
from django_celery_beat.schedulers import DatabaseScheduler


def lines(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, object]]:
    return [r.msg for r in caplog.records if isinstance(r.msg, dict) and r.msg['event'] == event]


@pytest.fixture
def pipe() -> MagicMock:
    pipe = MagicMock()
    # one task waiting per priority 0-10
    pipe.execute.return_value = [1] * 11
    return pipe


@pytest.fixture
def scheduler(pipe: MagicMock) -> QueueDepthScheduler:
    # without __init__, which loads the schedule from the database
    scheduler = object.__new__(QueueDepthScheduler)
    scheduler.app = SimpleNamespace(conf=SimpleNamespace(broker_url='redis://x', task_default_queue='celery'))
    scheduler._redis = MagicMock(pipeline=MagicMock(return_value=pipe))
    return scheduler


class TestQueueDepth:
    def test_depth_is_split_by_priority(
        self, scheduler: QueueDepthScheduler, pipe: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        pipe.execute.return_value = [1, 0, 0, 2, 3, 0, 0, 0, 0, 4, 5]

        scheduler.log_queue_depth()

        assert [c.args[0] for c in pipe.llen.call_args_list] == ['celery', *(f'celery:{n}' for n in range(1, 11))]
        (line,) = lines(caplog, 'celery_queue_depth')
        assert (line['depth'], line['depth_high'], line['depth_normal'], line['depth_low']) == (15, 3, 3, 9)

    def test_tick_samples_at_most_once_per_interval(
        self, scheduler: QueueDepthScheduler, caplog: pytest.LogCaptureFixture
    ) -> None:
        with (
            patch.object(DatabaseScheduler, 'tick', return_value=5) as parent_tick,
            patch('core.beat.time', monotonic=MagicMock(side_effect=[100, 105, 100 + SAMPLE_INTERVAL])),
        ):
            assert [scheduler.tick() for _ in range(3)] == [5, 5, 5]

        assert parent_tick.call_count == 3
        assert len(lines(caplog, 'celery_queue_depth')) == 2

    def test_redis_error_is_logged_once_and_never_breaks_beat(
        self, scheduler: QueueDepthScheduler, pipe: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        pipe.execute.side_effect = [ConnectionError('down'), ConnectionError('down'), [0] * 11, ConnectionError('down')]

        with (
            patch.object(DatabaseScheduler, 'tick', return_value=5),
            patch('core.beat.time', monotonic=MagicMock(side_effect=[0, 60, 120, 180])),
        ):
            assert [scheduler.tick() for _ in range(4)] == [5, 5, 5, 5]

        # two outages, one line each; the sample between them went through
        assert len(lines(caplog, 'celery_queue_depth_failed')) == 2
        assert len(lines(caplog, 'celery_queue_depth')) == 1
