import copy
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from gamedata.models.game_exchange import GameExchange
from gamedata.models.game_planet import GamePlanet, GamePlanetCOGCProgramChoices
from model_bakery import baker
from rest_framework.test import APIClient
from rest_framework_csv.renderers import CSVRenderer
from tests.fixtures.fxt_fio_ship_data import fio_ship_data
from tests.fixtures.fxt_fio_sites_data import fio_sites_data
from tests.fixtures.fxt_fio_storage_data import fio_storage_data
from tests.fixtures.fxt_fio_warehouse_data import fio_warehouse_data
from user.models import User
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
        exchange_analytics_factory(ticker='IRON', exchange_code='NC1', calendar_date=now, vwap_7d=50, avg_traded_7d=5)
        exchange_analytics_factory(ticker='GOLD', exchange_code='CI1', calendar_date=now, vwap_7d=0, avg_traded_7d=0)
        # exchange code not part of the tracked target list, must be excluded
        exchange_analytics_factory(ticker='VOID', exchange_code='BAD_EXC')

        exchange_factory(
            ticker_id='IRONNC1', ticker='IRON', exchange_code='NC1', ask=12.5, bid=11.5, supply=5, demand=42
        )

        response = api_client.get(_exchange_list_url())

        assert response.status_code == 200
        data = response.data
        assert len(data) == 3

        results = {item['ticker']: item for item in data}

        assert results['FUEL']['exchange_status'] == 'STALE'
        assert results['IRON']['exchange_status'] == 'ACTIVE'
        assert results['GOLD']['exchange_status'] == 'INACTIVE'
        assert results['IRON']['ticker_id'] == 'IRON.NC1'

        # live data merged in from GameExchange
        assert results['IRON']['ask'] == 12.5
        assert results['IRON']['bid'] == 11.5
        assert results['IRON']['supply'] == 5
        assert results['IRON']['demand'] == 42

        # no live data available defaults to 0.0
        assert results['FUEL']['ask'] == 0.0
        assert results['FUEL']['bid'] == 0.0

    def test_list_returns_duplicate_ticker_rows_on_sqlite(
        self, api_client: APIClient, exchange_analytics_factory: Callable[..., object]
    ) -> None:
        exchange_analytics_factory(ticker='H2O', exchange_code='AI1', date_epoch=1000)
        exchange_analytics_factory(ticker='H2O', exchange_code='AI1', date_epoch=5000)

        response = api_client.get(_exchange_list_url())

        h2o_records = [row for row in response.data if row['ticker'] == 'H2O']

        assert len(h2o_records) == 2
        assert h2o_records[0]['date_epoch'] == 5000
        assert h2o_records[1]['date_epoch'] == 1000

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
    def test_multiple_accepts_100_ids(self, api_client: APIClient) -> None:
        ids = [f'AB-{i:03d}c' for i in range(100)]

        response = api_client.post(reverse('data:planet-multiple'), data=ids, format='json')

        assert response.status_code == 200

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

    def test_multiple_is_not_cached(self, api_client: APIClient) -> None:
        response = api_client.post(reverse('data:planet-multiple'), data=['AB-001c'], format='json')
        assert not response.has_header('X-Cache-Hit')


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
        }
        assert entry['cogc_program_status'] is None
        assert entry['resources'] == [
            {
                'material_ticker': 'FEO',
                'resource_type': entry['resources'][0]['resource_type'],
                'daily_extraction': 12.3457,
                'max_daily_extraction': entry['resources'][0]['max_daily_extraction'],
            }
        ]

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
