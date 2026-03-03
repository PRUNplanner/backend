from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from tests.fixtures.fxt_fio_ship_data import fio_ship_data
from tests.fixtures.fxt_fio_sites_data import fio_sites_data
from tests.fixtures.fxt_fio_storage_data import fio_storage_data
from tests.fixtures.fxt_fio_warehouse_data import fio_warehouse_data

pytestmark = pytest.mark.django_db


def test_list_recipes(api_client, recipe_factory):
    recipe_factory(_quantity=3)

    response = api_client.get(reverse('data:recipe-list'))

    assert response.status_code == 200
    assert len(response.data) == 3


def test_list_materials(api_client, material_factory):
    material_factory(_quantity=3)

    response = api_client.get(reverse('data:material-list'))

    assert response.status_code == 200
    assert len(response.data) == 3


def test_list_buildings(api_client, building_factory):
    building_factory(_quantity=3)

    response = api_client.get(reverse('data:building-list'))

    assert response.status_code == 200
    assert len(response.data) == 3


class TestGamePlanetViewSet:
    def test_list(self, api_client, planet_factory):
        planet_factory(_quantity=3)

        response = api_client.get(reverse('data:planet-list'))

        assert response.status_code == 200
        assert len(response.data) == 3

    def test_retrieve(self, api_client, planet_factory):
        planet_natural_id = 'OT-580b'
        planet_factory(planet_natural_id=planet_natural_id)

        url = reverse('data:planet-detail', kwargs={'planet_natural_id': planet_natural_id})
        response = api_client.get(url)

        assert response.status_code == 200
        assert response.data['planet_natural_id'] == planet_natural_id

    def test_multiple(self, api_client, planet_factory):
        planet_natural_ids = ['OT-580b', 'ZV-759b', 'EW-688c']

        for pid in planet_natural_ids:
            planet_factory(planet_natural_id=pid)
        response = api_client.post(reverse('data:planet-multiple'), data=planet_natural_ids, format='json')

        assert response.status_code == 200
        assert len(response.data) == 3
        assert response.data[0]['planet_natural_id'] in planet_natural_ids
        assert response.data[1]['planet_natural_id'] in planet_natural_ids
        assert response.data[2]['planet_natural_id'] in planet_natural_ids

    def test_search_single(self, api_client, planet_factory):
        planet_natural_ids = ['OT-580b', 'OT-758c', 'EW-688c']

        for pid in planet_natural_ids:
            planet_factory(planet_natural_id=pid)

        response = api_client.get(reverse('data:planet-search-single', kwargs={'search_term': 'OT-'}))

        assert response.status_code == 200
        assert len(response.data) == 2
        assert response.data[0]['planet_natural_id'] in planet_natural_ids
        assert response.data[1]['planet_natural_id'] in planet_natural_ids

        response_short = api_client.get(reverse('data:planet-search-single', kwargs={'search_term': 'x'}))
        assert response_short.status_code == 400

    def test_popr(self, api_client, planet_factory, popr_factory):
        planet = planet_factory(planet_natural_id='OT-580b')

        response = api_client.get(reverse('data:planet-infrastructure', kwargs={'planet_natural_id': 'OT-580b'}))
        assert response.status_code == 404

        _popr = popr_factory(planet=planet)
        response_exist = api_client.get(reverse('data:planet-infrastructure', kwargs={'planet_natural_id': 'OT-580b'}))
        assert response_exist.status_code == 200

    def test_search(self, api_client, planet_factory):

        search_data = {
            'materials': [],
            'cogc_programs': [],
            'must_be_fertile': True,
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

        planet_factory(planet_natural_id='Fertile', fertility_type=True)
        planet_factory(planet_natural_id='Not-Fertile', fertility_type=False)

        response = api_client.post(reverse('data:planet-search'), data=search_data, format='json')

        assert response.status_code == 200
        assert len(response.data) == 1


class GameExchangeViewSet:
    def test_list_exchanges_logic(self, api_client, exchange_analytics_factory):
        url = reverse('data:exchange-list')  # Adjust namespace if needed
        now = timezone.now().date()
        stale_date = now - timedelta(days=5)

        # 1. Create a STALE record (Old date)
        exchange_analytics_factory(
            ticker='FUEL', exchange_code='AI1', calendar_date=stale_date, vwap_7d=100, avg_traded_7d=10
        )

        # 2. Create an ACTIVE record (Recent date + metrics > 0)
        exchange_analytics_factory(ticker='IRON', exchange_code='NC1', calendar_date=now, vwap_7d=50, avg_traded_7d=5)

        # 3. Create an INACTIVE record (Recent date but 0 metrics)
        exchange_analytics_factory(ticker='GOLD', exchange_code='CI1', calendar_date=now, vwap_7d=0, avg_traded_7d=0)

        # 4. Create a record with IGNORED exchange code
        exchange_analytics_factory(ticker='VOID', exchange_code='BAD_EXC')

        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK

        # We expect 3 valid records (AI1, NC1, CI1), ignoring BAD_EXC
        data = response.json()
        assert len(data) == 3

        # Verify Annotations
        # Use a dict lookup for easy verification
        results = {item['ticker']: item for item in data}

        assert results['FUEL']['exchange_status'] == 'STALE'
        assert results['IRON']['exchange_status'] == 'ACTIVE'
        assert results['GOLD']['exchange_status'] == 'INACTIVE'

        # Verify ticker_id Concat logic
        assert results['IRON']['ticker_id'] == 'IRON.NC1'

    def test_distinct_ordering_logic(self, api_client, exchange_analytics_factory):
        """
        In SQLite, we expect both records to be returned.
        We verify the order_by ensures the newest record is first.
        """
        url = reverse('data:exchange-list')

        exchange_analytics_factory(ticker='H2O', exchange_code='AI1', date_epoch=1000)
        exchange_analytics_factory(ticker='H2O', exchange_code='AI1', date_epoch=5000)

        response = api_client.get(url)
        data = response.json()

        h2o_records = [i for i in data if i['ticker'] == 'H2O']

        # Because we are in SQLite, we get 2 records instead of 1
        assert len(h2o_records) == 2

        # Crucial: Verify the first record is the one with the LATEST epoch (5000)
        assert h2o_records[0]['date_epoch'] == 5000
        assert h2o_records[1]['date_epoch'] == 1000

    def test_csv_export_format_and_headers(self, api_client, exchange_analytics_factory):

        exchange_analytics_factory(ticker='FUEL', exchange_code='AI1', date_epoch=12345)

        url = reverse('data:exchanges-list-csv')
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response['Content-Type'] == 'text/csv; charset=utf-8'

        content = response.content.decode('utf-8')
        lines = content.splitlines()

        expected_header = (
            'ticker,exchange_code,ticker_id,date_epoch,calendar_date,exchange_status,'
            'vwap_daily,vwap_7d,vwap_30d,traded_daily,sum_traded_7d,sum_traded_30d,'
            'avg_traded_7d,avg_traded_30d'
        )

        assert lines[0] == expected_header

        assert 'FUEL,AI1' in lines[1]


class TestGameStorageViewSet:
    def test_retrieve_storage(self, api_client, user_factory, fio_playerdata_factory):

        user_1 = user_factory(id=1)
        user_2 = user_factory(id=2)

        _fio_playerdata = fio_playerdata_factory(
            user=user_1,
            storage_data=fio_storage_data,
            site_data=fio_sites_data,
            warehouse_data=fio_warehouse_data,
            ship_data=fio_ship_data,
        )

        url = reverse('data:storage-retrieve')

        # user must be authenticated
        response_noauth = api_client.get(url)
        assert response_noauth.status_code == 401

        # user retrieves data
        response_good = api_client.as_user(user_1).get(url)
        assert response_good.status_code == 200

        # user has no data, gets 404
        response_404 = api_client.as_user(user_2).get(url)
        assert response_404.status_code == 404

        _fio_playerdata_wrong = fio_playerdata_factory(
            user=user_2,
        )
        response_validationerror = api_client.as_user(user_2).get(url)
        assert response_validationerror.status_code == 400


class TestExchangeCXPCViewSet:
    def test_cxpc_ticker(self, api_client, exchange_cxpc_factory):

        exchange_cxpc_factory(ticker='DW', exchange_code='AI1', _quantity=10)
        exchange_cxpc_factory(ticker='RAT', exchange_code='AI1', _quantity=5)

        url = reverse('data:cxpc-market-data-ticker', kwargs={'ticker': 'DW'})
        response = api_client.get(url)
        assert response.status_code == 200
        assert len(response.data) == 10

        exchange_cxpc_factory(ticker='DW', exchange_code='IC1', _quantity=3)

        url_code = reverse('data:cxpc-market-data-full', kwargs={'ticker': 'DW', 'exchange_code': 'IC1'})
        response_exchange = api_client.get(url_code)
        assert response_exchange.status_code == 200
        assert len(response_exchange.data) == 3

        url_wrong = reverse('data:cxpc-market-data-full', kwargs={'ticker': 'DW', 'exchange_code': 'foo'})
        response_wrong = api_client.get(url_wrong)
        assert response_wrong.status_code == 400
