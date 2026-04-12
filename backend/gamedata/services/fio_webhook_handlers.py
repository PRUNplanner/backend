from abc import ABC, abstractmethod

import orjson
import structlog
from django.utils import timezone
from django_redis import get_redis_connection
from structlog.typing import FilteringBoundLogger

from gamedata.fio.schemas.fio_webhook import FIOWebhookExchangeEndpointSchema
from gamedata.models.game_exchange import GameExchange


class BaseFIOWebhookHandler(ABC):
    log: FilteringBoundLogger

    @abstractmethod
    def process(self, data: list):
        """Each handler must implement its own processing logic"""
        pass


class FIOCXWebhookHandler(BaseFIOWebhookHandler):
    def __init__(self):
        self.log = structlog.get_logger().bind(handler='cx')

    SYNC_FIELDS = ['mm_buy', 'mm_sell', 'price_average', 'ask', 'bid', 'ask_count', 'bid_count', 'supply', 'demand']
    STREAM_ALLOWED_EXCHANGES = {'AI1', 'NC1', 'IC1', 'CI1'}
    STREAM_MAX_LEN = 500

    def process(self, data: list[FIOWebhookExchangeEndpointSchema]):

        updates_by_ticker = self._extract(data)

        if not updates_by_ticker:
            return

        # fetch & update logic
        ticker_ids = list(updates_by_ticker.keys())
        existing_records = {obj.ticker_id: obj for obj in GameExchange.objects.filter(ticker_id__in=ticker_ids)}

        if not existing_records:
            self.log.info('no_existing_tickers_found', count=len(ticker_ids))
            return

        # keep track of updates and redis pushes
        to_update_db = []
        redis_payloads = []

        for t_id, db_obj in existing_records.items():
            incoming = updates_by_ticker[t_id]

            if self._merge(db_obj, incoming):
                to_update_db.append(db_obj)

            if incoming.exchange_code in self.STREAM_ALLOWED_EXCHANGES:
                redis_payloads.append(incoming.pubsub_dump(worker_timestamp=timezone.now().isoformat()))

        # persisting
        if to_update_db:
            GameExchange.objects.bulk_update(to_update_db, fields=self.SYNC_FIELDS)

        if redis_payloads:
            self._push_to_redis(redis_payloads)

        self.log.info('sync_complete', db_updated=len(to_update_db), stream_pushed=len(redis_payloads))

    def _extract(self, data: list[FIOWebhookExchangeEndpointSchema]) -> dict[str, FIOWebhookExchangeEndpointSchema]:

        updates = {}
        for cx_info in data:
            t_id = f'{cx_info.material_ticker}.{cx_info.exchange_code}'
            updates[t_id] = cx_info
        return updates

    def _merge(self, db_obj: GameExchange, incoming: FIOWebhookExchangeEndpointSchema) -> bool:

        changed = False
        for field in self.SYNC_FIELDS:
            val = getattr(incoming, field, None)
            if val is not None and getattr(db_obj, field) != val:
                setattr(db_obj, field, val)
                changed = True
        return changed

    def _push_to_redis(self, payloads: list[dict]):

        r = get_redis_connection('default')
        with r.pipeline(transaction=False) as pipe:
            for p in payloads:
                pipe.xadd('stream:cx', {'payload': orjson.dumps(p)}, maxlen=self.STREAM_MAX_LEN, approximate=True)
            pipe.execute()
