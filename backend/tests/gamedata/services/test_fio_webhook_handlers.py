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


def _webhook_entry(exchange_code: str) -> FIOWebhookExchangeEndpointSchema:
    return FIOWebhookExchangeEndpointSchema.model_validate(
        {
            'CXEntryId': 'a' * 32,
            'MaterialId': 'b' * 32,
            'ExchangeId': 'c' * 32,
            'MaterialTicker': 'FE',
            'ExchangeCode': exchange_code,
            'CurrencyCode': 'NCC',
            'Timestamp': '2025-06-17T02:06:59.928389Z',
            'Demand': 42,
            'Supply': 5,
            'Traded': 3,
            'Ask': 12.5,
        }
    )


class TestFIOCXWebhookHandlerStream:
    """The SSE endpoint relays `stream:<channel>` entries' `payload`; the frontend reads channel `cx` (SSECXSchema)."""

    def test_pushes_streamed_exchanges_in_the_frontend_shape(self) -> None:
        for code in ('NC1', 'NC2'):
            baker.make('gamedata.GameExchange', ticker_id=f'FE.{code}', ticker='FE', exchange_code=code)

        with patch('gamedata.services.fio_webhook_handlers.get_redis_connection') as get_redis:
            FIOCXWebhookHandler().process([_webhook_entry('NC1'), _webhook_entry('NC2')])

        pipe = get_redis.return_value.pipeline.return_value.__enter__.return_value
        pipe.xadd.assert_called_once()  # NC2 is not streamed
        (stream, fields), options = pipe.xadd.call_args
        assert stream == 'stream:cx'
        assert options == {'maxlen': FIOCXWebhookHandler.STREAM_MAX_LEN, 'approximate': True}
        payload = orjson.loads(fields['payload'])
        assert payload['material_ticker'] == 'FE'
        assert payload['exchange_code'] == 'NC1'
        assert payload['ask'] == 12.5
        assert payload['timestamp'].endswith('Z')  # z.iso.datetime() accepts no offset
        assert None not in payload.values()  # optional fields are omitted, Zod's .optional() rejects null
        assert 'bid' not in payload
        pipe.execute.assert_called_once()
