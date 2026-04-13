import asyncio
import time
import uuid

import structlog
from django.conf import settings
from django.http import StreamingHttpResponse
from drf_spectacular.utils import extend_schema
from redis import asyncio as aioredis
from rest_framework.decorators import permission_classes
from rest_framework.permissions import AllowAny

logger = structlog.get_logger(__name__)


@extend_schema(
    summary='Real-time data stream (SSE)',
    description='Connect to this endpoint using EventSource to receive real-time updates.',
    responses={200: str},
)
@permission_classes([AllowAny])
async def sse_stream_view(request):
    channels = [c.strip() for c in request.GET.get('channels', 'beat').split(',') if c.strip()]
    redis_keys = {f'stream:{c}': request.headers.get('Last-Event-ID', '0') for c in channels}

    # unique connection ID for stats
    conn_id = str(uuid.uuid4())
    stats_key = 'stream:active_connections'

    redis_url = settings.CACHES['default']['LOCATION']

    async def event_generator():
        log = logger.bind(name='data_stream', channels=channels)
        log.info(action='stream_open')

        # initiate redis, pubsub and subscribe to channels
        redis = await aioredis.from_url(redis_url)

        total_sent_count = 0

        try:
            # register connection with current timestamp
            await redis.zadd(stats_key, {conn_id: time.time()})

            # loop
            while True:
                # XREAD expects a dict with { key: last_id }
                events = await redis.xread(redis_keys, count=1, block=5000)

                if events:
                    for stream_name, messages in events:
                        for message_id, data in messages:
                            last_id = message_id.decode('utf-8')

                            redis_keys[stream_name.decode('utf-8')] = last_id

                            payload = data[b'payload'].decode('utf-8')
                            yield f'id: {last_id}\ndata: {payload}\n\n'
                else:
                    # keep-alive + heartbeat in redis
                    await redis.zadd(stats_key, {conn_id: time.time()})
                    yield ': \n\n'

        # user left stream
        except asyncio.CancelledError:
            raise

        # cleanup
        finally:
            # prune this connection
            await redis.zrem(stats_key, conn_id)

            # prune stale connections (> 30s)
            await redis.zremrangebyscore(stats_key, 0, time.time() - 30)

            await redis.close()

            log.info(action='stream_close', messages_sent=total_sent_count)

    response = StreamingHttpResponse(event_generator(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'

    return response
