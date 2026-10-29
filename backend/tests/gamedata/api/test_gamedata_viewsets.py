import copy
import gzip
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import patch

import orjson
import pytest
from core.services.cache_manager import CacheManager
from django.core.cache.backends.locmem import LocMemCache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from gamedata.fio.schemas.fio_sites import FIOUserSiteSchema
from gamedata.gamedata_cache_manager import PLANET
from gamedata.models.game_exchange import GameExchange
from gamedata.models.game_planet import GamePlanet, GamePlanetCOGCProgramChoices
from gamedata.tasks import refresh_exchange_analytics
from model_bakery import baker
from rest_framework.test import APIClient
from rest_framework_csv.renderers import CSVRenderer
from rest_framework_simplejwt.tokens import AccessToken
from tests.fixtures.fxt_fio_ship_data import fio_ship_data
from tests.fixtures.fxt_fio_sites_data import fio_sites_data
from tests.fixtures.fxt_fio_storage_data import fio_storage_data
from tests.fixtures.fxt_fio_warehouse_data import fio_warehouse_data
from user.models import User, UserAPIKey
from user.models.configs import GlobalConfigWebhook, WebhookSenderChoices

pytestmark = pytest.mark.django_db


def _exchange_list_url() -> str:
    return reverse('data:exchange-list')


def _exchange_csv_url() -> str:
    return reverse('data:exchanges-list-csv')


def _webhook_url(token: uuid.UUID) -> str:
    return reverse('data:fio-webhook-ingest', kwargs={'token': token})


class TestGameExchangeViewSet:
    def test_list_annotates_status_and_merges_live_data(
        self,
        api_client: APIClient,
        exchange_analytics_factory: Callable[..., object],
        exchange_factory: Callable[..., GameExchange],
    ) -> None:
        now = timezone.now().date()
        stale_date = now - timedelta(days=5)

        exchange_analytics_factory(
            ticker='FUEL', exchange_code='AI1', calendar_date=stale_date, vwap_7d=100, avg_traded_7d=10
        )
        exchange_analytics_factory(ticker='FE', exchange_code='NC1', calendar_date=now, vwap_7d=50, avg_traded_7d=5)
        exchange_analytics_factory(ticker='GOLD', exchange_code='CI1', calendar_date=now, vwap_7d=0, avg_traded_7d=0)
        # exchange code not part of the tracked target list, must be excluded
        exchange_analytics_factory(ticker='VOID', exchange_code='BAD_EXC')

        exchange_factory(ticker_id='FE.NC1', ticker='FE', exchange_code='NC1', ask=12.5, bid=11.5, supply=5, demand=42)

        response = api_client.get(_exchange_list_url())

        assert response.status_code == 200
        data = response.data
        assert len(data) == 3

        results = {item['ticker']: item for item in data}

        assert results['FUEL']['exchange_status'] == 'STALE'
        assert results['FE']['exchange_status'] == 'ACTIVE'
        assert results['GOLD']['exchange_status'] == 'INACTIVE'
        assert results['FE']['ticker_id'] == 'FE.NC1'

        # live data merged in from GameExchange
        assert results['FE']['ask'] == 12.5
        assert results['FE']['bid'] == 11.5
        assert results['FE']['supply'] == 5
        assert results['FE']['demand'] == 42

        # no live data available defaults to 0.0
        assert results['FUEL']['ask'] == 0.0
        assert results['FUEL']['bid'] == 0.0

    @pytest.mark.skipif(connection.vendor != 'postgresql', reason='the materialized view exists on Postgres only')
    @pytest.mark.usefixtures('exchange_analytics_view')
    def test_list_serves_the_refreshed_view_one_row_per_pair(self, api_client: APIClient) -> None:
        today = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

        def cxpc(exchange_code: str, days_ago: int, volume: int, traded: int) -> None:
            date_epoch = int((today - timedelta(days=days_ago)).timestamp() * 1000)
            baker.make(
                'gamedata.GameExchangeCXPC',
                ticker='RAT',
                exchange_code=exchange_code,
                date_epoch=date_epoch,
                volume=volume,
                traded=traded,
            )

        baker.make('gamedata.GameMaterial', ticker='RAT')
        cxpc('AI1', days_ago=0, volume=100, traded=10)
        cxpc('AI1', days_ago=2, volume=300, traded=10)
        cxpc('CI1', days_ago=3, volume=50, traded=5)  # nothing traded today: the last price carries forward

        with patch('gamedata.tasks.gamedata_warm_market_summary.delay'):
            assert refresh_exchange_analytics() is True

        rows = api_client.get(_exchange_list_url()).data
        by_exchange = {row['exchange_code']: row for row in rows}

        assert len(rows) == len(by_exchange) == 5
        assert sorted(by_exchange) == ['AI1', 'CI1', 'IC1', 'NC1', 'UNIVERSE']
        assert by_exchange['AI1']['traded_daily'] == 10
        assert by_exchange['AI1']['vwap_daily'] == 10
        assert by_exchange['AI1']['sum_traded_7d'] == 20
        assert by_exchange['AI1']['vwap_7d'] == 20
        assert by_exchange['AI1']['exchange_status'] == 'ACTIVE'
        assert by_exchange['CI1']['vwap_daily'] == 10
        assert by_exchange['NC1']['vwap_7d'] == 0
        assert by_exchange['NC1']['exchange_status'] == 'INACTIVE'
        assert by_exchange['UNIVERSE']['sum_traded_7d'] == 25

    def test_list_empty_database_returns_empty_list(self, api_client: APIClient) -> None:
        response = api_client.get(_exchange_list_url())

        assert response.status_code == 200
        assert response.data == []

    def test_unauthenticated_access_is_allowed(self, api_client: APIClient) -> None:
        response = api_client.get(_exchange_list_url())

        assert response.status_code == 200


class TestGameExchangeCSVViewSet:
    def test_csv_export_format_and_headers(
        self, api_client: APIClient, exchange_analytics_factory: Callable[..., object]
    ) -> None:
        exchange_analytics_factory(ticker='FUEL', exchange_code='AI1', date_epoch=12345)

        response = api_client.get(_exchange_csv_url())

        assert response.status_code == 200
        assert response['Content-Type'] == 'text/csv; charset=utf-8'

        content = response.content.decode('utf-8')
        lines = content.splitlines()

        expected_header = (
            'ticker,exchange_code,ticker_id,date_epoch,calendar_date,exchange_status,'
            'vwap_daily,vwap_7d,vwap_30d,traded_daily,sum_traded_7d,sum_traded_30d,'
            'avg_traded_7d,avg_traded_30d,ask,bid,supply,demand'
        )

        assert lines[0] == expected_header
        assert 'FUEL,AI1' in lines[1]


class TestFIOWebhookIngest:
    def test_unknown_token_returns_404(self, api_client: APIClient) -> None:
        response = api_client.post(_webhook_url(uuid.uuid4()), data={'Data': []}, format='json')

        assert response.status_code == 404

    def test_inactive_config_returns_404(
        self, api_client: APIClient, webhook_config_factory: Callable[..., GlobalConfigWebhook]
    ) -> None:
        config = webhook_config_factory(sender=WebhookSenderChoices.FIOAPI, is_active=False)

        response = api_client.post(_webhook_url(config.path), data={'Data': []}, format='json')

        assert response.status_code == 404

    def test_invalid_payload_returns_400(
        self, api_client: APIClient, webhook_config_factory: Callable[..., GlobalConfigWebhook]
    ) -> None:
        config = webhook_config_factory(sender=WebhookSenderChoices.FIOAPI, is_active=True)

        response = api_client.post(_webhook_url(config.path), data={}, format='json')

        assert response.status_code == 400

    def test_valid_payload_accepted_updates_stats_and_queues_task(
        self, api_client: APIClient, webhook_config_factory: Callable[..., GlobalConfigWebhook]
    ) -> None:
        config = webhook_config_factory(
            sender=WebhookSenderChoices.FIOAPI, is_active=True, total_calls=3, last_received_at=None
        )

        with patch('gamedata.api.viewsets.gamedata_process_fio_webhook.delay') as mock_delay:
            response = api_client.post(_webhook_url(config.path), data={'Data': []}, format='json')

        assert response.status_code == 202
        mock_delay.assert_called_once_with({'Data': []})

        config.refresh_from_db()
        assert config.total_calls == 4
        assert config.last_received_at is not None


def _search_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        'materials': [],
        'cogc_programs': [],
        'must_be_fertile': False,
        'environment_rocky': True,
        'environment_gaseous': True,
        'environment_low_gravity': True,
        'environment_high_gravity': True,
        'environment_low_pressure': True,
        'environment_high_pressure': True,
        'environment_low_temperature': True,
        'environment_high_temperature': True,
        'must_have_localmarket': False,
        'must_have_chamberofcommerce': False,
        'must_have_warehouse': False,
        'must_have_administrationcenter': False,
        'must_have_shipyard': False,
    }
    payload.update(overrides)
    return payload


@pytest.mark.usefixtures('locmem_cache')
class TestGamePlanetViewSetMultiple:
    def test_multiple_and_retrieve_never_share_an_entry(
        self, api_client: APIClient, planet_factory: Callable[..., GamePlanet]
    ) -> None:
        planet_factory(planet_natural_id='OT-580b')
        multiple_url = reverse('data:planet-multiple')
        detail_url = reverse('data:planet-detail', kwargs={'planet_natural_id': 'OT-580b'})

        assert isinstance(api_client.post(multiple_url, data=['OT-580b'], format='json').data, list)
        assert isinstance(api_client.get(detail_url).data, dict)
        assert isinstance(api_client.get(detail_url).data, dict)
        assert isinstance(api_client.post(multiple_url, data=['OT-580b'], format='json').data, list)

    def test_multiple_returns_the_detail_bodies_in_request_order(
        self, api_client: APIClient, planet_factory: Callable[..., GamePlanet]
    ) -> None:
        for planet_id in ('OT-580b', 'AB-001c'):
            planet_factory(planet_natural_id=planet_id)

        response = api_client.post(
            reverse('data:planet-multiple'), data=['AB-001c', 'XX-000x', 'OT-580b', 'AB-001c'], format='json'
        )

        details = [
            orjson.loads(api_client.get(reverse('data:planet-detail', kwargs={'planet_natural_id': p})).content)
            for p in ('AB-001c', 'OT-580b')
        ]
        assert response.status_code == 200
        assert orjson.loads(response.content) == details

    def test_multiple_repeat_runs_no_queries_and_builds_only_new_planets(
        self, api_client: APIClient, planet_factory: Callable[..., GamePlanet], django_assert_num_queries
    ) -> None:
        for planet_id in ('OT-580b', 'AB-001c'):
            planet_factory(planet_natural_id=planet_id)
        url = reverse('data:planet-multiple')
        assert api_client.post(url, data=['OT-580b'], format='json')['X-Cache-Hit'] == '0'

        with django_assert_num_queries(0):
            response = api_client.post(url, data=['OT-580b'], format='json')
        assert response['X-Cache-Hit'] == '1'

        with CaptureQueriesContext(connection) as queries:
            response = api_client.post(url, data=['OT-580b', 'AB-001c'], format='json')
        assert [p['planet_natural_id'] for p in orjson.loads(response.content)] == ['OT-580b', 'AB-001c']
        assert 'AB-001c' in queries[0]['sql']
        assert not any('OT-580b' in q['sql'] for q in queries)

    def test_multiple_serves_new_data_after_invalidation_and_answers_304(
        self, api_client: APIClient, planet_factory: Callable[..., GamePlanet]
    ) -> None:
        planet = planet_factory(planet_natural_id='OT-580b', planet_name='Old')
        url = reverse('data:planet-multiple')
        old = api_client.post(url, data=['OT-580b'], format='json')
        assert old['Cache-Control'] == 'public, max-age=60'

        GamePlanet.objects.filter(pk=planet.pk).update(planet_name='New')
        CacheManager.invalidate(PLANET, 'OT-580b')
        new = api_client.post(url, data=['OT-580b'], format='json', HTTP_IF_NONE_MATCH=old['ETag'])

        assert new.status_code == 200
        assert orjson.loads(new.content)[0]['planet_name'] == 'New'
        assert new['ETag'] != old['ETag']
        unchanged = api_client.post(url, data=['OT-580b'], format='json', HTTP_IF_NONE_MATCH=new['ETag'])
        assert unchanged.status_code == 304
        assert unchanged.content == b''

    @pytest.mark.parametrize(('count', 'status'), [(200, 200), (201, 400)])
    def test_multiple_accepts_at_most_200_ids(self, api_client: APIClient, count: int, status: int) -> None:
        ids = [f'AB-{i:03d}c' for i in range(count)]
        assert api_client.post(reverse('data:planet-multiple'), data=ids, format='json').status_code == status


@pytest.mark.usefixtures('locmem_cache')
class TestGamedataCacheHits:
    def test_planet_list_cache_hit_runs_no_queries(
        self, api_client: APIClient, planet_factory: Callable[..., GamePlanet], django_assert_num_queries
    ) -> None:
        planet_factory(planet_natural_id='AB-001c')
        url = reverse('data:planet-list')
        api_client.get(url)

        with django_assert_num_queries(0):
            response = api_client.get(url)

        assert response['X-Cache-Hit'] == '1'

    def test_latest_popr_cache_hit_runs_no_queries(
        self,
        api_client: APIClient,
        planet_factory: Callable[..., GamePlanet],
        popr_factory: Callable[..., object],
        django_assert_num_queries,
    ) -> None:
        popr_factory(planet=planet_factory(planet_natural_id='AB-001c'))
        url = reverse('data:planet-infrastructure', kwargs={'planet_natural_id': 'AB-001c'})
        api_client.get(url)

        with django_assert_num_queries(0):
            response = api_client.get(url)

        assert response['X-Cache-Hit'] == '1'

    def test_exchange_csv_cache_hit_is_not_rerendered(
        self, api_client: APIClient, exchange_analytics_factory: Callable[..., object]
    ) -> None:
        exchange_analytics_factory(ticker='FUEL', exchange_code='AI1', date_epoch=12345)
        url = _exchange_csv_url()
        first = api_client.get(url)

        with patch.object(CSVRenderer, 'render', autospec=True, side_effect=CSVRenderer.render) as render:
            second = api_client.get(url)

        assert second['X-Cache-Hit'] == '1'
        assert second.content == first.content
        render.assert_not_called()


class TestGameStorageCacheHeaders:
    @pytest.mark.usefixtures('locmem_cache')
    def test_storage_response_is_private(
        self,
        api_client: APIClient,
        user_factory: Callable[..., User],
        fio_playerdata_factory: Callable[..., object],
    ) -> None:
        user = user_factory()
        fio_playerdata_factory(
            user=user,
            storage_data=fio_storage_data,
            site_data=fio_sites_data,
            warehouse_data=fio_warehouse_data,
            ship_data=fio_ship_data,
        )

        response = api_client.as_user(user).get(reverse('data:storage-retrieve'))  # ty:ignore[unresolved-attribute]

        assert response.status_code == 200
        assert 'private' in response['Cache-Control']
        assert 'public' not in response['Cache-Control']


class TestGameStorageViewSet:
    def test_null_or_missing_storage_items_return_empty_list(
        self,
        api_client: APIClient,
        user_factory: Callable[..., User],
        fio_playerdata_factory: Callable[..., object],
    ) -> None:
        user = user_factory()
        storage_data = copy.deepcopy(fio_storage_data)
        stores = [s for s in storage_data if s['Type'] == 'STORE']
        stores[0]['StorageItems'] = None  # ty:ignore[invalid-assignment]
        del stores[1]['StorageItems']
        fio_playerdata_factory(
            user=user,
            storage_data=storage_data,
            site_data=fio_sites_data,
            warehouse_data=fio_warehouse_data,
            ship_data=fio_ship_data,
        )

        response = api_client.as_user(user).get(reverse('data:storage-retrieve'))  # ty:ignore[unresolved-attribute]

        assert response.status_code == 200
        planets = response.data['storage_data']['planets'].values()
        assert all(isinstance(p['StorageItems'], list) for p in planets)
        assert sum(p['StorageItems'] == [] for p in planets) >= 2

    def test_sites_carry_site_id_and_building_created(
        self,
        api_client: APIClient,
        user_factory: Callable[..., User],
        fio_playerdata_factory: Callable[..., object],
    ) -> None:
        user = user_factory()
        fio_playerdata_factory(
            user=user,
            storage_data=fio_storage_data,
            site_data=fio_sites_data,
            warehouse_data=fio_warehouse_data,
            ship_data=fio_ship_data,
        )

        response = api_client.as_user(user).get(reverse('data:storage-retrieve'))  # ty:ignore[unresolved-attribute]

        assert response.status_code == 200
        sites = response.data['sites_data']
        fio_sites = [FIOUserSiteSchema.model_validate(s) for s in fio_sites_data]
        assert sites.keys() == {s.PlanetIdentifier for s in fio_sites}
        for fio_site in fio_sites:
            site = sites[fio_site.PlanetIdentifier]
            assert re.fullmatch(r'[0-9a-f]{32}', site['SiteId'])
            assert site['SiteId'] == fio_site.SiteId
            assert [b['BuildingCreated'] for b in site['Buildings']] == [b.BuildingCreated for b in fio_site.Buildings]
            # unchanged: both only present once a building was repaired
            assert all(('BuildingLastRepair' in b) == ('AgeDays' in b) for b in site['Buildings'])


class TestFIOWebhookIngestConcurrency:
    def test_counter_increment_is_atomic(
        self, api_client: APIClient, webhook_config_factory: Callable[..., GlobalConfigWebhook]
    ) -> None:
        config = webhook_config_factory(sender=WebhookSenderChoices.FIOAPI, is_active=True, total_calls=3)
        stale_config = GlobalConfigWebhook.objects.get(pk=config.pk)

        # other requests increment the counter after this request loaded its row
        GlobalConfigWebhook.objects.filter(pk=config.pk).update(total_calls=10)

        with (
            patch('gamedata.api.viewsets.get_object_or_404', return_value=stale_config),
            patch('gamedata.api.viewsets.gamedata_process_fio_webhook.delay'),
        ):
            response = api_client.post(_webhook_url(config.path), data={'Data': []}, format='json')

        assert response.status_code == 202
        config.refresh_from_db()
        assert config.total_calls == 11


class TestGamePlanetActiveCOGC:
    @pytest.mark.parametrize('now_seconds, expected_active', [(1.5, True), (3.0, False)])
    def test_active_cogc_is_evaluated_per_request(
        self,
        api_client: APIClient,
        planet_factory: Callable[..., GamePlanet],
        now_seconds: float,
        expected_active: bool,
    ) -> None:
        planet = planet_factory(planet_natural_id='AB-001c')
        program_type = GamePlanetCOGCProgramChoices.values[0]
        baker.make(
            'gamedata.GamePlanetCOGCProgram',
            planet=planet,
            program_type=program_type,
            start_epochms=1_000,
            end_epochms=2_000,
        )
        url = reverse('data:planet-detail', kwargs={'planet_natural_id': 'AB-001c'})

        with patch('django.utils.timezone.now', return_value=datetime.fromtimestamp(now_seconds, tz=UTC)):
            response = api_client.get(url)

        assert response.data['active_cogc_program_type'] == (program_type if expected_active else None)


@pytest.mark.usefixtures('locmem_cache')
class TestGamePlanetSearchIndex:
    URL = '/data/planets/search-index/'

    def test_url_is_not_captured_by_search_single(self) -> None:
        assert reverse('data:planet-search-index') == self.URL

    def test_returns_every_planet_with_the_index_fields(
        self, api_client: APIClient, planet_factory: Callable[..., GamePlanet]
    ) -> None:
        planet = planet_factory(planet_natural_id='AB-001c', cogc_program_status='')
        planet_factory(planet_natural_id='AB-002c')
        baker.make(
            'gamedata.GamePlanetResource',
            planet=planet,
            material_ticker='FEO',
            daily_extraction=12.345678,
            _bulk_create=True,
        )

        response = api_client.get(self.URL)

        assert response.status_code == 200
        assert sorted(p['planet_natural_id'] for p in response.data) == ['AB-001c', 'AB-002c']
        entry = next(p for p in response.data if p['planet_natural_id'] == 'AB-001c')
        assert set(entry) == {
            'planet_natural_id',
            'planet_name',
            'system_id',
            'surface',
            'gravity_type',
            'pressure_type',
            'temperature_type',
            'fertility',
            'has_localmarket',
            'has_chamberofcommerce',
            'has_warehouse',
            'has_administrationcenter',
            'has_shipyard',
            'cogc_program_status',
            'cogc_programs',
            'resources',
            *GamePlanet.PLOT_FIELDS,
        }
        assert entry['cogc_program_status'] is None
        # no plots refresh yet: unknown, not zero
        assert all(entry[f] is None for f in GamePlanet.PLOT_FIELDS)
        assert entry['resources'] == [
            {
                'material_ticker': 'FEO',
                'resource_type': entry['resources'][0]['resource_type'],
                'daily_extraction': 12.3457,
                'max_daily_extraction': entry['resources'][0]['max_daily_extraction'],
            }
        ]

    @pytest.mark.parametrize('url', [URL, '/data/planet/AB-001c/'])
    def test_returns_the_plots(
        self, url: str, api_client: APIClient, planet_factory: Callable[..., GamePlanet]
    ) -> None:
        planet_factory(
            planet_natural_id='AB-001c',
            plots_total=418,
            plots_used=418,
            ghost_sites=96,
            supports_ghost_sites=True,
            plots_updated=datetime(2026, 10, 24, 17, 37, tzinfo=UTC),
        )

        data = api_client.get(url).json()
        entry = data[0] if isinstance(data, list) else data

        assert {f: entry[f] for f in GamePlanet.PLOT_FIELDS} == {
            'plots_total': 418,
            'plots_used': 418,
            'ghost_sites': 96,
            'supports_ghost_sites': True,
            'plots_updated': '2026-10-24T17:37:00Z',
        }

    def test_drops_programs_that_ended_before_build_time(
        self, api_client: APIClient, planet_factory: Callable[..., GamePlanet]
    ) -> None:
        planet = planet_factory(planet_natural_id='AB-001c')
        program_type = GamePlanetCOGCProgramChoices.values[0]
        for start, end in [(1_000, 2_000), (1_000, 5_000), (4_000, 9_000)]:
            baker.make(
                'gamedata.GamePlanetCOGCProgram',
                planet=planet,
                program_type=program_type,
                start_epochms=start,
                end_epochms=end,
            )

        with patch('django.utils.timezone.now', return_value=datetime.fromtimestamp(3.0, tz=UTC)):
            response = api_client.get(self.URL)

        windows = sorted((p['start_epochms'], p['end_epochms']) for p in response.data[0]['cogc_programs'])
        assert windows == [(1_000, 5_000), (4_000, 9_000)]

    def test_second_call_is_a_cache_hit_without_queries(
        self, api_client: APIClient, planet_factory: Callable[..., GamePlanet], django_assert_num_queries
    ) -> None:
        planet_factory(planet_natural_id='AB-001c')
        assert api_client.get(self.URL)['X-Cache-Hit'] == '0'

        with django_assert_num_queries(0):
            response = api_client.get(self.URL)

        assert response['X-Cache-Hit'] == '1'

    @pytest.mark.parametrize('planet_count', [1, 6])
    def test_query_count_does_not_depend_on_planet_count(
        self,
        api_client: APIClient,
        planet_factory: Callable[..., GamePlanet],
        django_assert_num_queries,
        planet_count: int,
    ) -> None:
        for i in range(planet_count):
            planet = planet_factory(planet_natural_id=f'AB-{i:03d}c')
            baker.make(
                'gamedata.GamePlanetResource', planet=planet, material_ticker='FEO', _quantity=2, _bulk_create=True
            )
            baker.make('gamedata.GamePlanetCOGCProgram', planet=planet, end_epochms=2**62)

        # planets, resources, cogc programs
        with django_assert_num_queries(3):
            api_client.get(self.URL)


PLANET_ID = 'AB-001c'

# (url name, url kwargs, POST body or None for GET)
PUBLIC_ROUTES: list[tuple[str, dict[str, str], object | None]] = [
    ('material-list', {}, None),
    ('recipe-list', {}, None),
    ('building-list', {}, None),
    ('exchange-list', {}, None),
    ('exchanges-list-csv', {}, None),
    ('planet-list', {}, None),
    ('planet-search-index', {}, None),
    ('planet-detail', {'planet_natural_id': PLANET_ID}, None),
    ('planet-infrastructure', {'planet_natural_id': PLANET_ID}, None),
    ('planet-search-single', {'search_term': PLANET_ID}, None),
    ('planet-multiple', {}, [PLANET_ID]),
    ('planet-search', {}, _search_payload()),
    ('cxpc-market-data-ticker', {'ticker': 'FUEL'}, None),
    ('cxpc-market-data-full', {'ticker': 'FUEL', 'exchange_code': 'AI1'}, None),
    ('cxpc-sparklines', {'exchange_code': 'UNIVERSE'}, None),
    ('market-summary', {}, None),
]


def _expired_jwt(user: User) -> str:
    token = AccessToken.for_user(user)
    token.set_exp(lifetime=-timedelta(minutes=1))
    return f'Bearer {token}'


def _authorization(kind: str, user: User) -> str | None:
    if kind == 'expired':
        return _expired_jwt(user)
    if kind == 'malformed':
        return 'Bearer garbage'
    if kind == 'revoked':
        header = f'Bearer {AccessToken.for_user(user)}'
        user.delete()
        return header
    if kind == 'api_key':
        _, key = UserAPIKey.objects.create_key(name='script', user=user)
        return f'Api-Key {key}'
    return None


@pytest.mark.usefixtures('locmem_cache')
class TestPublicEndpointsSkipAuthentication:
    @pytest.fixture(autouse=True)
    def _planet(self, planet_factory: Callable[..., GamePlanet], popr_factory: Callable[..., object]) -> None:
        popr_factory(planet=planet_factory(planet_natural_id=PLANET_ID))

    @pytest.mark.parametrize('credentials', ['expired', 'malformed', 'revoked', 'api_key', 'none'])
    @pytest.mark.parametrize(('name', 'kwargs', 'body'), PUBLIC_ROUTES, ids=[route[0] for route in PUBLIC_ROUTES])
    def test_any_authorization_header_is_ignored(
        self,
        api_client: APIClient,
        user_factory: Callable[..., User],
        name: str,
        kwargs: dict[str, str],
        body: object | None,
        credentials: str,
    ) -> None:
        header = _authorization(credentials, user_factory())
        if header:
            api_client.credentials(HTTP_AUTHORIZATION=header)
        url = reverse(f'data:{name}', kwargs=kwargs)

        response = api_client.get(url) if body is None else api_client.post(url, data=body, format='json')

        assert response.status_code == 200

    def test_matching_etag_with_expired_jwt_returns_304(
        self, api_client: APIClient, user_factory: Callable[..., User]
    ) -> None:
        url = reverse('data:planet-detail', kwargs={'planet_natural_id': PLANET_ID})
        etag = api_client.get(url)['ETag']
        api_client.credentials(HTTP_AUTHORIZATION=_expired_jwt(user_factory()))

        response = api_client.get(url, HTTP_IF_NONE_MATCH=etag)

        assert response.status_code == 304

    def test_storage_still_rejects_expired_jwt(self, api_client: APIClient, user_factory: Callable[..., User]) -> None:
        api_client.credentials(HTTP_AUTHORIZATION=_expired_jwt(user_factory()))

        response = api_client.get(reverse('data:storage-retrieve'))

        assert response.status_code == 401

    def test_planet_cache_hit_with_valid_jwt_runs_no_queries(
        self, api_client: APIClient, user_factory: Callable[..., User], django_assert_num_queries
    ) -> None:
        url = reverse('data:planet-detail', kwargs={'planet_natural_id': PLANET_ID})
        api_client.get(url)
        api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user_factory())}')

        with django_assert_num_queries(0):
            response = api_client.get(url)

        assert response.status_code == 200
        assert response['X-Cache-Hit'] == '1'


class TestGameStorageThrottle:
    def test_storage_routes_share_30_a_minute(
        self, api_client: APIClient, user_factory: Callable[..., User], locmem_cache: LocMemCache
    ) -> None:
        client = api_client.as_user(user_factory())  # ty:ignore[unresolved-attribute]
        urls = [reverse(f'data:storage-{name}') for name in ('retrieve', 'rows', 'csv')]
        # no FIO data: 404, which still counts
        for i in range(30):
            assert client.get(urls[i % 3]).status_code == 404

        for url in urls:
            response = client.get(url)
            assert response.status_code == 429
            assert int(response['Retry-After']) > 0
        other = APIClient()
        other.force_authenticate(user_factory())
        assert other.get(urls[0]).status_code == 404


class TestGameStorageRowsViewSet:
    @pytest.fixture
    def storage_user(self, user_factory: Callable[..., User], fio_playerdata_factory: Callable[..., object]) -> User:
        user = user_factory()
        fio_playerdata_factory(
            user=user,
            storage_data=fio_storage_data,
            site_data=fio_sites_data,
            warehouse_data=fio_warehouse_data,
            ship_data=fio_ship_data,
        )
        return user

    def test_rows_match_storage_endpoint(self, api_client: APIClient, storage_user: User) -> None:
        client = api_client.as_user(storage_user)  # ty:ignore[unresolved-attribute]
        storage = client.get(reverse('data:storage-retrieve')).data['storage_data']

        response = client.get(reverse('data:storage-rows'))

        assert response.status_code == 200
        expected = sorted(
            (location, item['MaterialTicker'], item['MaterialAmount'])
            for group in ('planets', 'warehouses', 'ships')
            for location, store in storage[group].items()
            for item in store['StorageItems']
        )
        rows = response.data['rows']
        assert sorted((r['location'], r['ticker'], r['amount']) for r in rows) == expected
        assert expected
        assert {r['storage_type'] for r in rows} <= {'STORE', 'WAREHOUSE_STORE', 'SHIP_STORE'}
        assert all(r['ticker'] for r in rows)

    def test_csv_matches_json_rows(self, api_client: APIClient, storage_user: User) -> None:
        client = api_client.as_user(storage_user)  # ty:ignore[unresolved-attribute]
        rows = client.get(reverse('data:storage-rows')).data['rows']

        response = client.get(reverse('data:storage-csv'))

        assert response.status_code == 200
        assert response['Content-Type'] == 'text/csv; charset=utf-8'
        lines = response.content.decode().splitlines()
        assert lines[0] == 'location,storage_type,storage_name,ticker,amount,updated_at'
        assert len(lines) == len(rows) + 1
        first = rows[0]
        assert lines[1].split(',')[3:5] == [first['ticker'], str(first['amount'])]

    def test_blocked_materials_without_ticker_are_dropped(
        self,
        api_client: APIClient,
        user_factory: Callable[..., User],
        fio_playerdata_factory: Callable[..., object],
    ) -> None:
        user = user_factory()
        storage_data = copy.deepcopy(fio_storage_data)
        store = next(s for s in storage_data if s['Type'] == 'STORE' and s.get('StorageItems'))
        cast(list[dict[str, object]], store['StorageItems'])[0]['MaterialTicker'] = None
        fio_playerdata_factory(
            user=user,
            storage_data=storage_data,
            site_data=fio_sites_data,
            warehouse_data=fio_warehouse_data,
            ship_data=fio_ship_data,
        )

        rows = api_client.as_user(user).get(reverse('data:storage-rows')).data['rows']  # ty:ignore[unresolved-attribute]

        assert all(r['ticker'] is not None for r in rows)

    @pytest.mark.parametrize('route', ['data:storage-rows', 'data:storage-csv'])
    def test_no_fio_data_is_404(self, api_client: APIClient, user_factory: Callable[..., User], route: str) -> None:
        response = api_client.as_user(user_factory()).get(reverse(route))  # ty:ignore[unresolved-attribute]

        assert response.status_code == 404

    @pytest.mark.parametrize('route', ['data:storage-rows', 'data:storage-csv'])
    def test_requires_auth(self, api_client: APIClient, route: str) -> None:
        assert api_client.get(reverse(route)).status_code == 401

    def test_csv_with_api_key_in_query(self, api_client: APIClient, storage_user: User) -> None:
        _, key = UserAPIKey.objects.create_key(name='sheet', user=storage_user)

        response = api_client.get(
            reverse('data:storage-csv'),
            {'api_key': key},
            HTTP_USER_AGENT='Mozilla/5.0 (compatible; GoogleDocs; apps-spreadsheets; +http://docs.google.com)',
            HTTP_ACCEPT_ENCODING='gzip',
        )

        assert response.status_code == 200
        assert response['Content-Encoding'] == 'gzip'
        body = gzip.decompress(response.content).decode()
        assert body.startswith('location,storage_type,storage_name,ticker,amount,updated_at\r\n')

    def test_csv_accepts_text_csv(self, api_client: APIClient, storage_user: User) -> None:
        response = api_client.as_user(storage_user).get(  # ty:ignore[unresolved-attribute]
            reverse('data:storage-csv'), HTTP_ACCEPT='text/csv'
        )

        assert response.status_code == 200
        assert response['Content-Type'] == 'text/csv; charset=utf-8'
