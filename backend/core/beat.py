import time

import redis
import structlog
from django_celery_beat.schedulers import DatabaseScheduler

logger = structlog.get_logger(__name__)

SAMPLE_INTERVAL = 60  # seconds
# kombu's redis transport keeps one list per priority step (priority_steps 0-10, sep ':'): priority 0 in the
# bare queue name, the others in '<queue>:<n>'. Lower number = served first.
PRIORITIES = range(11)


class QueueDepthScheduler(DatabaseScheduler):
    """Logs how many tasks wait in the broker, about once a minute.

    Sampled in beat, not as a task: the worker is single-process, so a backed-up queue would also delay the
    task measuring it. Tasks the worker already took (running, or held for an eta or rate limit) are not counted.
    """

    _next_sample = 0.0
    _sample_failed = False
    _redis: redis.Redis | None = None

    def tick(self, *args, **kwargs):
        now = time.monotonic()
        if now >= self._next_sample:
            self._next_sample = now + SAMPLE_INTERVAL
            self.log_queue_depth()
        return super().tick(*args, **kwargs)

    def log_queue_depth(self) -> None:
        # beat must keep scheduling whatever happens here
        try:
            if self._redis is None:
                # short timeouts: a hanging Redis must not stall the tick
                self._redis = redis.Redis.from_url(self.app.conf.broker_url, socket_timeout=2, socket_connect_timeout=2)
            queue = self.app.conf.task_default_queue
            pipe = self._redis.pipeline(transaction=False)
            for priority in PRIORITIES:
                pipe.llen(f'{queue}:{priority}' if priority else queue)
            sizes: list[int] = pipe.execute()
        except Exception:
            # once per outage, not once a minute
            if not self._sample_failed:
                logger.exception('celery_queue_depth_failed')
            self._sample_failed = True
            return

        self._sample_failed = False
        logger.info(
            'celery_queue_depth',
            depth=sum(sizes),
            depth_high=sum(sizes[:4]),
            depth_normal=sum(sizes[4:7]),
            depth_low=sum(sizes[7:]),
        )
