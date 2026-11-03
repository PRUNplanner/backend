import asyncio
import time
from collections.abc import AsyncGenerator, Iterator
from unittest.mock import patch

import pytest
from django.http import HttpResponseBase, StreamingHttpResponse
from django.test import Client, RequestFactory
from django.urls import reverse
from drf_spectacular.generators import SchemaGenerator
from gamedata.api import sse
from pytest_django.fixtures import SettingsWrapper
from redis.exceptions import ConnectionError as RedisConnectionError
from structlog.testing import capture_logs

REAL_REDIS = sse._redis

Event = tuple[bytes, list[tuple[bytes, dict[bytes, bytes]]]]


class FakeRedis:
    """In-memory stand-in for the redis.asyncio calls the stream makes; state is shared by all clients."""

    sets: dict[str, dict[str, float]] = {}
    reads: list[list[Event] | Exception | None] = []
    closed = 0

    async def zadd(self, key: str, mapping: dict[str, float]) -> None:
        await asyncio.sleep(0)  # let parallel opens interleave like real network calls
        self.sets.setdefault(key, {}).update(mapping)

    async def zrem(self, key: str, member: str) -> None:
        self.sets.get(key, {}).pop(member, None)

    async def zremrangebyscore(self, key: str, low: float, high: float) -> None:
        members = self.sets.get(key, {})
        for member in [m for m, score in members.items() if low <= score <= high]:
            del members[member]

    async def eval(self, script: str, numkeys: int, key: str, member: str, cap: str, stale_after: str) -> int:
        """sse._CLAIM_SLOT, which Redis runs atomically: no await between the count and the add."""
        await asyncio.sleep(0)
        now = time.time()
        members = self.sets.setdefault(key, {})
        for stale in [m for m, score in members.items() if score <= now - int(stale_after)]:
            del members[stale]
        if len(members) >= int(cap):
            return 0
        members[member] = now
        return 1

    async def expire(self, key: str, seconds: int) -> None:
        pass

    async def xread(self, streams: dict[str, str], count: int, block: int) -> list[Event] | None:
        read = FakeRedis.reads.pop(0) if FakeRedis.reads else None
        if isinstance(read, Exception):
            raise read
        return read

    async def aclose(self) -> None:
        FakeRedis.closed += 1


@pytest.fixture(autouse=True)
def fake_redis() -> Iterator[None]:
    FakeRedis.sets, FakeRedis.reads, FakeRedis.closed = {}, [], 0
    with patch.object(sse, '_redis', FakeRedis), patch.object(sse, '_open_streams', 0):
        yield


async def open_stream(ip: str = '10.0.0.1', channels: str = 'cx', last_event_id: str | None = None) -> HttpResponseBase:
    headers = {'Last-Event-ID': last_event_id} if last_event_id else {}
    request = RequestFactory().get('/data/stream/', {'channels': channels}, headers=headers, REMOTE_ADDR=ip)
    return await sse.sse_stream_view(request)


def stream(response: HttpResponseBase) -> AsyncGenerator[str]:
    assert isinstance(response, StreamingHttpResponse)
    return response._iterator  # ty: ignore[unresolved-attribute]; the view's own generator, so a test can close it like a disconnect does


class TestValidation:
    @pytest.mark.parametrize('channels', ['', 'totally_bogus_channel_xyz', 'cx,beat'])
    def test_unknown_or_missing_channel_is_400(self, channels: str) -> None:
        response = asyncio.run(open_stream(channels=channels))

        assert response.status_code == 400

    @pytest.mark.parametrize('event_id', ['abc', '1-2; drop', '$', '-1-0'])
    def test_bad_last_event_id_is_400(self, event_id: str) -> None:
        response = asyncio.run(open_stream(last_event_id=event_id))

        assert response.status_code == 400

    def test_cx_opens_an_event_stream(self) -> None:
        response = asyncio.run(open_stream(last_event_id='1700000000000-0'))

        assert response.status_code == 200
        assert response['Content-Type'] == 'text/event-stream'
        assert response['Cache-Control'] == 'no-cache'

    def test_unknown_channel_through_the_url_is_400(self) -> None:
        response = Client().get(reverse('data:data-stream'), {'channels': 'bogus'})

        assert response.status_code == 400


class TestStream:
    def test_sends_events_then_heartbeats_and_logs_open_and_close(self) -> None:
        FakeRedis.reads = [[(b'stream:cx', [(b'5-0', {b'payload': b'{"t":1}'})])], None]

        async def scenario() -> list[str]:
            body = stream(await open_stream())
            chunks = [await anext(body), await anext(body)]
            await body.aclose()
            return chunks

        with capture_logs() as logs:
            chunks = asyncio.run(scenario())

        assert chunks == ['id: 5-0\ndata: {"t":1}\n\n', ': \n\n']
        assert [line['event'] for line in logs] == ['sse_stream_opened', 'sse_stream_closed']
        assert logs[1]['messages_sent'] == 1
        assert FakeRedis.sets[sse.STATS_KEY] == {}
        assert FakeRedis.closed == 2  # the cap check's client and the stream's

    def test_redis_error_still_frees_the_slots(self) -> None:
        FakeRedis.reads = [RedisConnectionError('gone')]

        async def scenario() -> None:
            body = stream(await open_stream())
            with pytest.raises(RedisConnectionError):
                await anext(body)

        asyncio.run(scenario())

        assert sse._open_streams == 0
        assert FakeRedis.sets['stream:ip:10.0.0.1'] == {}


class TestCaps:
    def test_sixth_stream_from_one_ip_is_429_until_one_closes(self) -> None:
        async def scenario() -> tuple[int, int, int]:
            bodies = [stream(await open_stream()) for _ in range(sse.MAX_STREAMS_PER_IP)]
            for body in bodies:
                await anext(body)  # heartbeat: registered

            over = (await open_stream()).status_code
            other_ip = (await open_stream(ip='10.0.0.2')).status_code
            await bodies[0].aclose()
            freed = (await open_stream()).status_code
            for body in bodies[1:]:
                await body.aclose()
            return over, other_ip, freed

        assert asyncio.run(scenario()) == (429, 200, 200)
        assert sse._open_streams == 0

    def test_stale_streams_do_not_count(self) -> None:
        FakeRedis.sets['stream:ip:10.0.0.1'] = {f'dead-{i}': 1.0 for i in range(sse.MAX_STREAMS_PER_IP)}

        assert asyncio.run(open_stream()).status_code == 200

    def test_parallel_burst_from_one_ip_gets_no_more_than_the_cap(self) -> None:
        async def scenario() -> list[int]:
            responses = await asyncio.gather(*(open_stream() for _ in range(sse.MAX_STREAMS_PER_IP + 1)))
            return sorted(response.status_code for response in responses)

        assert asyncio.run(scenario()) == [200] * sse.MAX_STREAMS_PER_IP + [429]
        assert len(FakeRedis.sets['stream:ip:10.0.0.1']) == sse.MAX_STREAMS_PER_IP

    def test_redis_down_is_503(self) -> None:
        with patch.object(FakeRedis, 'eval', side_effect=RedisConnectionError('Too many connections')):
            assert asyncio.run(open_stream()).status_code == 503

    def test_worker_cap_is_429(self) -> None:
        with patch.object(sse, '_open_streams', sse.MAX_STREAMS_PER_WORKER):
            assert asyncio.run(open_stream()).status_code == 429


class TestPool:
    def test_streams_share_one_pool(self, settings: SettingsWrapper) -> None:
        settings.CACHES = {'default': {'LOCATION': 'redis://cache:6379/1'}}
        with patch.object(sse, '_redis_pool', None), patch.object(sse.aioredis.ConnectionPool, 'from_url') as from_url:
            clients = [REAL_REDIS() for _ in range(3)]

        from_url.assert_called_once()
        assert {id(client.connection_pool) for client in clients} == {id(from_url.return_value)}


def test_stream_is_in_the_openapi_schema() -> None:
    """A plain async view is skipped by drf-spectacular; the postprocessing hook documents it."""
    operation = SchemaGenerator().get_schema(request=None, public=True)['paths']['/data/stream/']['get']

    assert 'text/event-stream' in operation['responses']['200']['content']
    assert [p['name'] for p in operation['parameters'] if p['in'] == 'query'] == ['channels']
