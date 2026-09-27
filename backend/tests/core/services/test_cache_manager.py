import decimal
import gzip
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

import orjson
import pytest
import structlog
from core.services.cache_manager import CacheManager, CacheNamespace
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse
from django_structlog import signals
from model_bakery import baker
from rest_framework.test import APIClient

PUBLIC = CacheNamespace('test:public', 60)
PRIVATE = CacheNamespace('test:private', 60, private=True)


def _default_build() -> dict[str, object]:
    return {'price': decimal.Decimal('10.50'), 'id': uuid4()}


def _respond(
    ns: CacheNamespace = PUBLIC,
    gzip_ok: bool = False,
    build: Callable[[], object] = _default_build,
    scope: int | None = None,
    fmt: str = 'json',
    csv_header: list[str] | None = None,
) -> HttpResponse:
    request = RequestFactory().get('/', headers={'Accept-Encoding': 'gzip, deflate'} if gzip_ok else {})
    return CacheManager.respond(request, ns, 'endpoint', build=build, scope=scope, fmt=fmt, csv_header=csv_header)


class TestKey:
    def test_raw_input_stays_out_of_the_key(self) -> None:
        term = 'x' * 10_000
        key = CacheManager.key(PUBLIC, 'search', term, term, scope=1)
        assert term[:20] not in key
        assert len(key) < 120

    def test_endpoint_parts_and_format_all_separate_entries(self) -> None:
        keys = {
            CacheManager.key(PUBLIC, 'retrieve', 'OT-580b'),
            CacheManager.key(PUBLIC, 'multiple', 'OT-580b'),
            CacheManager.key(PUBLIC, 'retrieve', 'ot-580b'),
            CacheManager.key(PUBLIC, 'retrieve', 'OT-580b', fmt='csv'),
        }
        assert len(keys) == 4


@pytest.mark.usefixtures('locmem_cache')
class TestInvalidate:
    def test_bump_only_changes_its_own_scope(self) -> None:
        a, b, unscoped = (CacheManager.key(PRIVATE, 'e', scope=s) for s in (1, 2, None))

        CacheManager.invalidate(PRIVATE, 1)
        CacheManager.invalidate(PRIVATE, 1)

        bumped = CacheManager.key(PRIVATE, 'e', scope=1)
        assert bumped != a
        assert ':v3:' in bumped
        assert CacheManager.key(PRIVATE, 'e', scope=2) == b
        assert CacheManager.key(PRIVATE, 'e') == unscoped

    @patch('core.services.cache_manager.cache')
    def test_version_counter_never_expires(self, mock_cache) -> None:
        mock_cache.add.return_value = True
        CacheManager.invalidate(PUBLIC)
        mock_cache.add.assert_called_once_with('test:public:ver', 2, timeout=None)


@pytest.mark.usefixtures('locmem_cache')
class TestRespond:
    def test_miss_then_hit(self) -> None:
        first = _respond()
        second = _respond(build=lambda: pytest.fail('a hit must not rebuild'))

        assert (first['X-Cache-Hit'], second['X-Cache-Hit']) == ('0', '1')
        assert first.content == second.content
        assert orjson.loads(first.content)['price'] == 10.5

    @pytest.mark.parametrize('cached', [False, True])
    def test_gzip_body_matches_plain_body(self, cached: bool) -> None:
        if cached:
            _respond()
        zipped = _respond(gzip_ok=True, build=lambda: {'n': 1})
        plain = _respond(build=lambda: {'n': 1})

        assert zipped['Content-Encoding'] == 'gzip'
        assert not plain.has_header('Content-Encoding')
        assert gzip.decompress(zipped.content) == plain.content
        assert zipped['ETag'] == plain['ETag']
        assert zipped['ETag'].startswith('W/"')
        assert zipped['Vary'] == plain['Vary'] == 'Accept-Encoding'

    def test_cache_control(self) -> None:
        assert _respond()['Cache-Control'] == 'public, max-age=60'
        assert _respond(PRIVATE, scope=1)['Cache-Control'] == 'private, no-cache'

    def test_private_namespace_needs_a_scope(self) -> None:
        with pytest.raises(AssertionError):
            _respond(PRIVATE)

    def test_csv(self) -> None:
        response = _respond(build=lambda: [{'a': 1, 'b': 2}], fmt='csv', csv_header=['b', 'a'])
        assert response['Content-Type'] == 'text/csv; charset=utf-8'
        assert response.content.splitlines() == [b'b,a', b'2,1']

    def test_concurrent_misses_rebuild_once(self) -> None:
        workers = 5
        build_calls: list[int] = []
        barrier = threading.Barrier(workers)

        def build() -> dict[str, bool]:
            build_calls.append(1)
            time.sleep(0.2)  # a slow rebuild, e.g. the full planet list
            return {'ok': True}

        def request() -> bytes:
            barrier.wait()
            return _respond(build=build).content

        with ThreadPoolExecutor(max_workers=workers) as pool:
            bodies = list(pool.map(lambda _: request(), range(workers)))

        assert bodies == [b'{"ok":true}'] * workers
        assert len(build_calls) == 1


@pytest.mark.django_db
@pytest.mark.usefixtures('locmem_cache')
class TestHttpCaching:
    @pytest.mark.parametrize(
        'url',
        [reverse('data:material-list'), reverse('data:planet-search-single', kwargs={'search_term': 'mon'})],
        ids=['cached', 'uncached'],
    )
    def test_if_none_match_gets_304(self, api_client: APIClient, url: str) -> None:
        first = api_client.get(url)
        assert first.status_code == 200
        assert first.has_header('ETag')

        second = api_client.get(url, HTTP_IF_NONE_MATCH=first['ETag'])
        assert second.status_code == 304
        assert second.content == b''

    def test_uncached_responses_default_to_private_no_cache(self, api_client: APIClient, user_factory) -> None:
        api_client.force_authenticate(user_factory())
        response = api_client.get(reverse('user:user_preferences'))
        assert response['Cache-Control'] == 'private, no-cache'

    def test_gzip_hit_is_sent_as_stored(self, api_client: APIClient) -> None:
        baker.make('gamedata.GameMaterial', _quantity=3)
        url = reverse('data:material-list')
        plain = api_client.get(url)
        zipped = api_client.get(url, HTTP_ACCEPT_ENCODING='gzip')

        assert zipped['X-Cache-Hit'] == '1'
        assert zipped['Content-Encoding'] == 'gzip'
        assert gzip.decompress(zipped.content) == plain.content

    def test_request_log_carries_namespace_and_hit(self, api_client: APIClient) -> None:
        seen: list[dict[str, object]] = []

        def snapshot(**_: object) -> None:
            seen.append(structlog.contextvars.get_contextvars())

        signals.bind_extra_request_finished_metadata.connect(snapshot, weak=False)
        try:
            api_client.get(reverse('data:material-list'))
            api_client.get(reverse('data:material-list'))
        finally:
            signals.bind_extra_request_finished_metadata.disconnect(snapshot)

        assert [(s['cache_ns'], s['cache_hit']) for s in seen] == [
            ('gamedata:materials', False),
            ('gamedata:materials', True),
        ]
