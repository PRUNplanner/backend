import uuid
from collections.abc import Callable
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from gamedata.models.game_exchange import GameExchange
from rest_framework.test import APIClient
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
