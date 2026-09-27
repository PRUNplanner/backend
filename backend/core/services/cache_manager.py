import decimal
import time
from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

import orjson
import structlog
from django.core.cache import cache as django_cache
from django.http import HttpResponse
from django.utils.cache import patch_cache_control
from django_redis.cache import RedisCache
from rest_framework.response import Response
from rest_framework_csv.renderers import CSVRenderer

logger = structlog.get_logger(__name__)
cache = cast(RedisCache, django_cache)


class CacheManager:
    BASE_KEY = 'BASE'

    # stampede protection: one request rebuilds a missing key, the others poll for its result
    REBUILD_LOCK_TIMEOUT = 30
    REBUILD_WAIT_SECONDS = 3.0
    REBUILD_POLL_SECONDS = 0.05

    @classmethod
    def make_key(cls, *parts: str | int | UUID) -> str:
        safe_parts = [str(p) for p in parts if p is not None]
        return ':'.join([cls.BASE_KEY, *safe_parts])

    @classmethod
    def get(cls, key: str) -> Any:
        return cache.get(key)

    @classmethod
    def set(cls, key: str, value: Any, timeout: int = 300) -> None:
        cache.set(key, value, timeout)

    @classmethod
    def delete(cls, key: str) -> None:
        logger.info('cache_key_purged', key=key)
        cache.delete(key)

    @classmethod
    def delete_pattern(cls, pattern: str) -> None:
        logger.info('cache_pattern_purged', pattern=pattern)
        cache.delete_pattern(pattern)

    @classmethod
    def add(cls, key: str, content: Any, timeout: int | None) -> bool:
        return cache.add(key, content, timeout=timeout)

    @classmethod
    def incr(cls, key: str) -> int:
        return cache.incr(key)

    # Response handling
    @classmethod
    def get_response(cls, key: str, timeout: int = 300) -> HttpResponse | None:
        wrapped = cls.get(key)
        if not wrapped:
            return None

        data = wrapped.get('data')

        response = HttpResponse(data, content_type='application/json')
        response['X-Cache-Hit'] = '1'
        response['Cache-Control'] = f'max-age={timeout}, public'

        return response

    @classmethod
    def build_response(cls, data: Any, timeout: int = 300) -> Response:
        response = Response(data)
        response['X-Cache-Hit'] = '0'
        response['Cache-Control'] = f'max-age={timeout}, public'
        return response

    @classmethod
    def _rebuild(cls, key: str, func: Callable[[], Any], timeout: int, fmt: str, csv_header: list[str] | None) -> bytes:
        """Builds and caches the payload. Concurrent misses wait for a single builder instead of all rebuilding."""
        lock_key = f'{key}:rebuild-lock'
        owns_lock = cls.add(lock_key, 1, cls.REBUILD_LOCK_TIMEOUT)

        if not owns_lock:
            deadline = time.monotonic() + cls.REBUILD_WAIT_SECONDS
            while time.monotonic() < deadline:
                time.sleep(cls.REBUILD_POLL_SECONDS)
                if cached_data := cls.get(key):
                    return cached_data
            # the builder is slow or died, build ourselves

        try:
            data = orjson.dumps(
                func(),
                default=lambda obj: (
                    float(obj) if isinstance(obj, decimal.Decimal) else str(obj) if isinstance(obj, UUID) else None
                ),
            )
            if fmt == 'csv':
                # rendered from the json round trip, so values match the json payload
                context = {'header': csv_header} if csv_header else {}
                data = CSVRenderer().render(orjson.loads(data), renderer_context=context)
            cls.set(key, data, timeout)
            return data
        finally:
            if owns_lock:
                cache.delete(lock_key)

    @classmethod
    def get_or_set_response(
        cls,
        key: str,
        func: Callable[[], Any],
        timeout: int = 300,
        fmt: str = 'json',
        private: bool = False,
        csv_header: list[str] | None = None,
    ) -> HttpResponse:
        """Serves pre-rendered bytes from the cache. `private` keeps per-user payloads out of shared caches."""
        cached_data = cls.get(key)
        data_to_return = cached_data or cls._rebuild(key, func, timeout, fmt, csv_header)

        content_type = 'text/csv; charset=utf-8' if fmt == 'csv' else 'application/json'
        response = HttpResponse(data_to_return, content_type=content_type)
        response['X-Cache-Hit'] = '1' if cached_data else '0'
        if private:
            patch_cache_control(response, private=True, max_age=timeout)
        else:
            patch_cache_control(response, public=True, max_age=timeout)
        return response
