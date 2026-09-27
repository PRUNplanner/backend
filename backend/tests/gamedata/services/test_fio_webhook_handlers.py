from unittest.mock import patch

import orjson
import pytest
from django.urls import reverse
from gamedata.fio.schemas.fio_webhook import FIOWebhookExchangeEndpointSchema
from gamedata.services.fio_webhook_handlers import FIOCXWebhookHandler
from model_bakery import baker
from rest_framework.test import APIClient

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('locmem_cache')]


def _process(**fields: float) -> None:
    incoming = FIOWebhookExchangeEndpointSchema.model_construct(
        None, material_ticker='FE', exchange_code='NC1', **fields
    )
    with (
        patch.object(FIOWebhookExchangeEndpointSchema, 'pubsub_dump', return_value={}),
        patch.object(FIOCXWebhookHandler, '_push_to_redis'),
    ):
        FIOCXWebhookHandler().process([incoming])


class TestFIOCXWebhookHandlerCache:
    @pytest.fixture(autouse=True)
    def exchange(self, exchange_analytics_factory) -> None:
        exchange_analytics_factory(ticker='FE', exchange_code='NC1')
        baker.make('gamedata.GameExchange', ticker_id='FE.NC1', ticker='FE', exchange_code='NC1', ask=1, bid=1)

    def test_changed_rows_refresh_json_and_csv(self, api_client: APIClient) -> None:
        json_url, csv_url = reverse('data:exchange-list'), reverse('data:exchanges-list-csv')
        api_client.get(json_url)
        api_client.get(csv_url)

        _process(ask=2, bid=1.5)

        row = orjson.loads(api_client.get(json_url).content)[0]
        assert (row['ask'], row['bid']) == (2, 1.5)
        csv = api_client.get(csv_url)
        assert csv['X-Cache-Hit'] == '0'
        assert b',2.0,1.5,' in csv.content

    def test_unchanged_rows_keep_the_cache(self, api_client: APIClient) -> None:
        url = reverse('data:exchange-list')
        api_client.get(url)

        _process(ask=1)

        assert api_client.get(url)['X-Cache-Hit'] == '1'
