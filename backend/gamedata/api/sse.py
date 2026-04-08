import asyncio

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
    raw_channels = request.GET.get('channels', 'beat')
    channels = [c.strip() for c in raw_channels.split(',') if c.strip()]

    redis_url = settings.CACHES['default']['LOCATION']

    async def event_generator():
        log = logger.bind(name='data_stream', channels=channels)
        log.info(action='stream_open')

        # initiate redis, pubsub and subscribe to channels
        redis = await aioredis.from_url(redis_url)
        pubsub = redis.pubsub()
        await pubsub.subscribe(*channels)

        total_sent_count = 0

        # loop
        try:
            while True:
                # timeout will yield loop
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)

                if message:
                    total_sent_count += 1
                    data = message['data'].decode('utf-8')
                    yield f'data: {data}\n\n'
                else:
                    # ping empty to keep connection alive
                    yield ': \n\n'

        # user left stream
        except asyncio.CancelledError:
            raise

        # cleanup
        finally:
            await pubsub.unsubscribe(*channels)
            await redis.close()

            log.info(action='stream_close', messages_sent=total_sent_count)

    response = StreamingHttpResponse(event_generator(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'

    return response
