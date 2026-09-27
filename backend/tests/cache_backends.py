import fnmatch
from collections import OrderedDict
from threading import Lock
from typing import ClassVar

from django.core.cache.backends.locmem import LocMemCache


class PatternLocMemCache(LocMemCache):
    """
    LocMemCache with django-redis' `delete_pattern`, so cache hits and
    invalidation can be tested for real without a Redis server.

    Every pattern deletion is recorded in `pattern_calls`, which stands in for
    a full-keyspace SCAN on Redis.
    """

    pattern_calls: ClassVar[list[str]] = []

    # LocMemCache internals, undeclared in django-stubs
    _cache: OrderedDict[str, bytes]
    _expire_info: dict[str, float | None]
    _lock: Lock

    def delete_pattern(self, pattern: str, version: int | None = None) -> int:
        PatternLocMemCache.pattern_calls.append(pattern)
        full_pattern = self.make_key(pattern, version=version)

        with self._lock:
            matched = [key for key in self._cache if fnmatch.fnmatchcase(key, full_pattern)]
            for key in matched:
                del self._cache[key]
                self._expire_info.pop(key, None)

        return len(matched)
