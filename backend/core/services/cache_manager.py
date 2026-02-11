import decimal
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

logger = structlog.get_logger(__name__)
cache = cast(RedisCache, django_cache)


class CacheManager:
    BASE_KEY = 'BASE'

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
    def get_or_set_response(cls, key: str, func: Callable[[], Any], timeout: int = 300) -> HttpResponse:
        cached_data = cls.get(key)

        if cached_data:
            # cached_data: bytes from orjson
            response = HttpResponse(cached_data, content_type='application/json')
            response['X-Cache-Hit'] = '1'
        else:
            data = func()

            # orjson -> dump to bytes.
            json_bytes = orjson.dumps(
                data,
                default=lambda obj: (
                    float(obj) if isinstance(obj, decimal.Decimal) else str(obj) if isinstance(obj, UUID) else None
                ),
            )

            cls.set(key, json_bytes, timeout)
            response = HttpResponse(json_bytes, content_type='application/json')
            response['X-Cache-Hit'] = '0'

        patch_cache_control(response, public=True, max_age=timeout)
        return response
