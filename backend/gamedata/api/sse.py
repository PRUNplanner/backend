import asyncio
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable
from typing import cast

import structlog
from django.conf import settings
from django.http import HttpRequest, HttpResponseBase, JsonResponse, StreamingHttpResponse
from redis import asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.typing import KeyT, StreamIdT
from rest_framework.throttling import BaseThrottle

logger = structlog.get_logger(__name__)

ALLOWED_CHANNELS = frozenset({'cx'})
MAX_STREAMS_PER_IP = 5
MAX_STREAMS_PER_WORKER = 200
# a stream heartbeats every 5 s; members older than this are streams that died without cleanup
STALE_AFTER = 30
STATS_KEY = 'stream:active_connections'
_EVENT_ID = re.compile(r'^\d+-\d+$')

_redis_pool: aioredis.ConnectionPool | None = None
_open_streams = 0


def _pool() -> aioredis.ConnectionPool:
    """One connection pool per process, shared by all streams."""
    global _redis_pool
    if _redis_pool is None:
        # a stream holds one connection; headroom for the slot claims of requests being opened
        _redis_pool = aioredis.ConnectionPool.from_url(
            settings.CACHES['default']['LOCATION'], max_connections=MAX_STREAMS_PER_WORKER + 20
        )
    return _redis_pool


def _redis() -> aioredis.Redis:
    return aioredis.Redis(connection_pool=_pool())


async def _touch(redis: aioredis.Redis, ip_key: str, conn_id: str) -> None:
    now = time.time()
    await redis.zadd(STATS_KEY, {conn_id: now})
    await redis.zadd(ip_key, {conn_id: now})
    await redis.expire(ip_key, STALE_AFTER * 2)


# prune, count and add in one atomic step on Redis's clock, so parallel opens across workers can't share a slot
_CLAIM_SLOT = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - tonumber(ARGV[3]))
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
    return 0
end
redis.call('ZADD', KEYS[1], now, ARGV[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]) * 2)
return 1
"""


async def _claim_ip_slot(ip_key: str, conn_id: str) -> bool:
    redis = _redis()
    try:
        # the stubs type eval for the sync client too
        claimed = cast(
            Awaitable[int], redis.eval(_CLAIM_SLOT, 1, ip_key, conn_id, str(MAX_STREAMS_PER_IP), str(STALE_AFTER))
        )
        return await claimed == 1
    finally:
        await redis.aclose()


def add_stream_to_schema(
    result: dict[str, dict[str, object]], generator: object, request: object, public: bool
) -> dict[str, dict[str, object]]:
    """drf-spectacular postprocessing hook: the stream is a plain async Django view, which the generator skips."""
    error = {
        'content': {
            'application/json': {
                'schema': {'type': 'object', 'properties': {'detail': {'type': 'string'}}, 'required': ['detail']}
            }
        }
    }
    result['paths']['/data/stream/'] = {
        'get': {
            'operationId': 'data_stream_retrieve',
            'summary': 'Real-time data stream (SSE)',
            'description': (
                'Server-sent events for EventSource. Each event is `id: <stream id>` and `data: <JSON payload>`; a '
                '`: ` comment line every 5 s keeps the connection alive. Send the last seen id as `Last-Event-ID` '
                f'to resume. At most {MAX_STREAMS_PER_IP} open streams per IP.'
            ),
            'tags': ['data'],
            'parameters': [
                {
                    'in': 'query',
                    'name': 'channels',
                    'required': True,
                    'description': f'Comma separated, one or more of: {", ".join(sorted(ALLOWED_CHANNELS))}',
                    'schema': {'type': 'string'},
                },
                {
                    'in': 'header',
                    'name': 'Last-Event-ID',
                    'required': False,
                    'description': 'Resume after this event id (`<ms>-<seq>`)',
                    'schema': {'type': 'string', 'pattern': _EVENT_ID.pattern},
                },
            ],
            'responses': {
                '200': {
                    'content': {'text/event-stream': {'schema': {'type': 'string'}}},
                    'description': 'Event stream',
                },
                '400': {**error, 'description': 'Unknown channel or invalid Last-Event-ID'},
                '429': {**error, 'description': 'Too many open streams'},
                '503': {**error, 'description': 'Stream unavailable'},
            },
        }
    }
    return result


def _error(status: int, detail: str) -> JsonResponse:
    return JsonResponse({'detail': detail}, status=status)


async def sse_stream_view(request: HttpRequest) -> HttpResponseBase:
    channels = [c.strip() for c in request.GET.get('channels', '').split(',') if c.strip()]
    if not channels or not ALLOWED_CHANNELS.issuperset(channels):
        return _error(400, f'channels must be one or more of: {", ".join(sorted(ALLOWED_CHANNELS))}')

    last_id = request.headers.get('Last-Event-ID', '0')
    if last_id != '0' and not _EVENT_ID.match(last_id):
        return _error(400, 'invalid Last-Event-ID')

    # unique connection ID for stats and the per-IP cap
    conn_id = str(uuid.uuid4())
    ip_key = f'stream:ip:{BaseThrottle().get_ident(request)}'

    # the worker count is an early reject; the hard per-worker bound is the pool's max_connections. A slot claimed
    # by a client that leaves before the stream starts ages out after STALE_AFTER.
    if _open_streams >= MAX_STREAMS_PER_WORKER:
        return _error(429, 'too many open streams')
    try:
        if not await _claim_ip_slot(ip_key, conn_id):
            return _error(429, 'too many open streams')
    except RedisConnectionError:
        # Redis down, or this worker's pool is full
        return _error(503, 'stream unavailable')

    redis_keys: dict[KeyT, StreamIdT] = {f'stream:{c}': last_id for c in channels}

    async def event_generator() -> AsyncIterator[str]:
        global _open_streams
        log = logger.bind(channels=channels)
        log.info('sse_stream_opened')
        started = time.monotonic()
        _open_streams += 1
        redis = _redis()

        total_sent_count = 0

        try:
            await _touch(redis, ip_key, conn_id)

            while True:
                # XREAD expects a dict with { key: last_id }
                events = await redis.xread(redis_keys, count=1, block=5000)

                if events:
                    for stream_name, messages in events:
                        for message_id, data in messages:
                            event_id = message_id.decode('utf-8')

                            redis_keys[stream_name.decode('utf-8')] = event_id

                            payload = data[b'payload'].decode('utf-8')
                            total_sent_count += 1
                            yield f'id: {event_id}\ndata: {payload}\n\n'
                else:
                    # keep-alive + heartbeat in redis
                    await _touch(redis, ip_key, conn_id)
                    yield ': \n\n'

        # user left stream
        except asyncio.CancelledError:
            raise

        finally:
            _open_streams -= 1
            try:
                await redis.zrem(STATS_KEY, conn_id)
                await redis.zrem(ip_key, conn_id)
                # prune stale connections
                await redis.zremrangebyscore(STATS_KEY, 0, time.time() - STALE_AFTER)
            finally:
                # returns the connection to the pool; the pool stays open
                await redis.aclose()

                log.info(
                    'sse_stream_closed',
                    messages_sent=total_sent_count,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )

    response = StreamingHttpResponse(event_generator(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'

    return response
