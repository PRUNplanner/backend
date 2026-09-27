import decimal
import gzip
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import blake2b
from uuid import UUID

import orjson
import structlog
from django.core.cache import cache
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.utils.cache import patch_cache_control, patch_vary_headers
from rest_framework_csv.renderers import CSVRenderer

logger = structlog.get_logger(__name__)

type CacheEntry = tuple[str, bytes]  # (weak etag of the raw body, gzipped body)

ACCEPTS_GZIP = re.compile(r'\bgzip\b')
PUBLIC_MAX_AGE = 60


@dataclass(frozen=True)
class CacheNamespace:
    """An invalidation unit: bumping its version drops every entry built under it."""

    name: str
    ttl: int
    private: bool = False  # per-user payloads, the scope must be the user id


class CacheManager:
    """
    Rendered responses cached per namespace. Keys are `{ns}[:{scope}]:v{version}:{endpoint}:{digest}`:
    the endpoint name keeps endpoints apart, the digest keeps request input out of the key, and the
    version counter (no TTL, so volatile-lru never evicts it) is the only way to invalidate.
    """

    # stampede protection: one request rebuilds a missing key, the others poll for its result
    REBUILD_LOCK_TIMEOUT = 30
    REBUILD_WAIT_SECONDS = 3.0
    REBUILD_POLL_SECONDS = 0.05

    @staticmethod
    def _prefix(ns: CacheNamespace, scope: int | str | None) -> str:
        return ns.name if scope is None else f'{ns.name}:{scope}'

    @classmethod
    def key(
        cls,
        ns: CacheNamespace,
        endpoint: str,
        *parts: str | int | UUID | None,
        scope: int | str | None = None,
        fmt: str = 'json',
    ) -> str:
        prefix = cls._prefix(ns, scope)
        version = cache.get(f'{prefix}:ver') or 1
        digest = blake2b(orjson.dumps([fmt, *parts], option=orjson.OPT_SORT_KEYS), digest_size=8).hexdigest()
        return f'{prefix}:v{version}:{endpoint}:{digest}'

    @classmethod
    def invalidate(cls, ns: CacheNamespace, scope: int | str | None = None) -> None:
        key = f'{cls._prefix(ns, scope)}:ver'
        # no TTL: a counter that expires starts over at 1 and serves entries from before the last bump
        if not cache.add(key, 2, timeout=None):
            cache.incr(key)
        logger.info('cache_invalidated', cache_ns=ns.name, scope=scope)

    @classmethod
    def invalidate_on_commit(cls, ns: CacheNamespace, scope: int | str | None = None) -> None:
        transaction.on_commit(lambda: cls.invalidate(ns, scope))

    @classmethod
    def _rebuild(
        cls, key: str, build: Callable[[], object], ttl: int, fmt: str, csv_header: list[str] | None
    ) -> CacheEntry:
        """Builds and caches the entry. Concurrent misses wait for a single builder instead of all rebuilding."""
        lock_key = f'{key}:rebuild-lock'
        owns_lock = cache.add(lock_key, 1, cls.REBUILD_LOCK_TIMEOUT)

        if not owns_lock:
            deadline = time.monotonic() + cls.REBUILD_WAIT_SECONDS
            while time.monotonic() < deadline:
                time.sleep(cls.REBUILD_POLL_SECONDS)
                if entry := cache.get(key):
                    return entry
            # the builder is slow or died, build ourselves

        try:
            body = orjson.dumps(
                build(),
                default=lambda obj: (
                    float(obj) if isinstance(obj, decimal.Decimal) else str(obj) if isinstance(obj, UUID) else None
                ),
            )
            if fmt == 'csv':
                # rendered from the json round trip, so values match the json payload
                context = {'header': csv_header} if csv_header else {}
                body = CSVRenderer().render(orjson.loads(body), renderer_context=context)
            # weak: the gzip and the plain body share it
            entry = (f'W/"{blake2b(body, digest_size=16).hexdigest()}"', gzip.compress(body, compresslevel=9, mtime=0))
            cache.set(key, entry, ttl)
            return entry
        finally:
            if owns_lock:
                cache.delete(lock_key)

    @classmethod
    def respond(
        cls,
        request: HttpRequest,
        ns: CacheNamespace,
        endpoint: str,
        *parts: str | int | UUID | None,
        build: Callable[[], object],
        scope: int | str | None = None,
        fmt: str = 'json',
        csv_header: list[str] | None = None,
    ) -> HttpResponse:
        """Serves the pre-gzipped entry, or its plain body to clients that don't accept gzip."""
        assert scope is not None or not ns.private, f'{ns.name} is private, its scope must be the user id'

        key = cls.key(ns, endpoint, *parts, scope=scope, fmt=fmt)
        cached: CacheEntry | None = cache.get(key)
        etag, gzipped = cached or cls._rebuild(key, build, ns.ttl, fmt, csv_header)
        structlog.contextvars.bind_contextvars(cache_ns=ns.name, cache_hit=cached is not None)

        content_type = 'text/csv; charset=utf-8' if fmt == 'csv' else 'application/json'
        if ACCEPTS_GZIP.search(request.headers.get('Accept-Encoding', '')):
            response = HttpResponse(gzipped, content_type=content_type)
            response['Content-Encoding'] = 'gzip'  # GZipMiddleware leaves it alone
        else:
            response = HttpResponse(gzip.decompress(gzipped), content_type=content_type)

        patch_vary_headers(response, ['Accept-Encoding'])
        response['ETag'] = etag
        response['X-Cache-Hit'] = '1' if cached else '0'
        if ns.private:
            patch_cache_control(response, private=True, no_cache=True)
        else:
            patch_cache_control(response, public=True, max_age=PUBLIC_MAX_AGE)
        return response
