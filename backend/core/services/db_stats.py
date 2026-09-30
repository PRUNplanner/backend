"""Query count and time of the current request or task, for its log line."""

import time
from collections.abc import Callable
from contextvars import ContextVar

from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.signals import connection_created
from django.dispatch import receiver

# [queries, seconds]. A mutable list, so the threads ASGI runs sync code in (each with a copy of the context and
# its own DB connection) add to the same counter. None outside a request or task: nothing is counted.
_stats: ContextVar[list[float] | None] = ContextVar('db_stats', default=None)


def reset() -> None:
    _stats.set([0, 0.0])


def snapshot() -> dict[str, float]:
    stats = _stats.get()
    if stats is None:
        return {}
    return {'db_queries': int(stats[0]), 'db_ms': round(stats[1] * 1000, 2)}


def count_query(
    execute: Callable[[str, object, bool, dict[str, object]], object],
    sql: str,
    params: object,
    many: bool,
    context: dict[str, object],
) -> object:
    stats = _stats.get()
    if stats is None:
        return execute(sql, params, many, context)

    started = time.perf_counter()
    try:
        return execute(sql, params, many, context)
    finally:
        stats[0] += 1
        stats[1] += time.perf_counter() - started


@receiver(connection_created, dispatch_uid='db_stats_install')
def install(connection: BaseDatabaseWrapper, **kwargs: object) -> None:
    # sent on every connect (CONN_MAX_AGE = 0: every request and task), the wrapper list outlives the connection
    if not any(wrapper is count_query for wrapper in connection.execute_wrappers):
        connection.execute_wrappers.append(count_query)
