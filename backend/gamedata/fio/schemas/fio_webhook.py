from datetime import datetime
from typing import Literal, Union

from pydantic import BaseModel, Field, field_validator

from gamedata.fio.schemas.fio_exchange import EXCHANGE_CODES


# Reference: https://gitlab.com/fnar/fio/fioapi/-/blob/main/FIOAPI/Controllers/CXController.cs
class FIOWebhookExchangeEndpointSchema(BaseModel):
    class FIOExchangeOrderSchema(BaseModel):
        company_id: str = Field(..., min_length=32, max_length=32, alias='CompanyId')
        company_name: str = Field(..., max_length=200, alias='CompanyName')
        company_code: str = Field(..., min_length=1, max_length=10, alias='CompanyCode')
        item_count: int | None = Field(default=None, ge=1, alias='ItemCount')
        item_cost: float = Field(ge=0.0, alias='ItemCost')

    class FIOExchangeBuyOrderSchema(FIOExchangeOrderSchema):
        cx_buy_order_id: str = Field(..., min_length=32, max_length=32, alias='CXBuyOrderId')

    class FIOExchangeSellOrderSchema(FIOExchangeOrderSchema):
        cx_sell_order_id: str = Field(..., min_length=32, max_length=32, alias='CXSellOrderId')

    # required
    ## cx info
    cx_entry_id: str = Field(..., min_length=32, max_length=32, alias='CXEntryId')
    material_id: str = Field(..., min_length=32, max_length=32, alias='MaterialId')
    exchange_id: str = Field(..., min_length=32, max_length=32, alias='ExchangeId')
    material_ticker: str = Field(..., min_length=1, max_length=3, alias='MaterialTicker')
    exchange_code: EXCHANGE_CODES = Field(alias='ExchangeCode')
    currency_code: str = Field(..., min_length=3, max_length=3, alias='CurrencyCode')
    timestamp: datetime = Field(..., alias='Timestamp')

    ## price data
    demand: int = Field(..., ge=0, alias='Demand')
    supply: int = Field(..., ge=0, alias='Supply')
    traded: int = Field(..., ge=0, alias='Traded')

    # order books
    buy_orders: list[FIOExchangeBuyOrderSchema] = Field(default_factory=list, alias='BuyOrders')
    sell_orders: list[FIOExchangeSellOrderSchema] = Field(default_factory=list, alias='SellOrders')

    # optional
    price_time: datetime | None = Field(default=None, alias='PriceTime')

    ## pricing info
    price: float | None = Field(default=None, ge=0.0, alias='Price')
    high: float | None = Field(default=None, ge=0.0, alias='High')
    low: float | None = Field(default=None, ge=0.0, alias='Low')

    ask: float | None = Field(default=None, ge=0.0, alias='Ask')
    ask_count: int | None = Field(default=None, ge=0, alias='AskCount')
    bid: float | None = Field(default=None, ge=0.0, alias='Bid')
    bid_count: int | None = Field(default=None, ge=0, alias='BidCount')
    price_average: float | None = Field(default=None, ge=0.0, alias='PriceAverage')

    mm_buy: float | None = Field(default=None, ge=0.0, alias='MMBuy')
    mm_sell: float | None = Field(default=None, ge=0.0, alias='MMSell')

    volume: float | None = Field(default=None, ge=0.0, alias='Volume')

    all_time_high: float | None = Field(default=None, ge=0.0, alias='AllTimeHigh')
    all_time_low: float | None = Field(default=None, ge=0.0, alias='AllTimeLow')
    narrow_price_band_low: float | None = Field(default=None, ge=0.0, alias='NarrowPriceBandLow')
    narrow_price_band_high: float | None = Field(default=None, ge=0.0, alias='NarrowPriceBandHigh')
    wide_price_band_low: float | None = Field(default=None, ge=0.0, alias='WidePriceBandLow')
    wide_price_band_high: float | None = Field(default=None, ge=0.0, alias='WidePriceBandHigh')

    def pubsub_dump(self, **extras):
        data = self.model_dump(
            include={
                'material_ticker': True,
                'exchange_code': True,
                'timestamp': True,
                'demand': True,
                'supply': True,
                'traded': True,
                'buy_orders': {'__all__': {'company_name', 'company_code', 'item_count', 'item_cost'}},
                'sell_orders': {'__all__': {'company_name', 'company_code', 'item_count', 'item_cost'}},
                # optionals
                'price': True,
                'ask': True,
                'ask_count': True,
                'bid': True,
                'bid_count': True,
                'price_average': True,
                'volume': True,
                'mm_buy': True,
                'mm_sell': True,
            },
            mode='json',
            exclude_none=True,
        )

        data.update(extras)
        return data


class FIOWebhookExchangeEndpoint(BaseModel):
    Endpoint: Literal['/cx']
    Data: list[FIOWebhookExchangeEndpointSchema]


FIOWebhookEndpointUnion = Union[FIOWebhookExchangeEndpoint]  # noqa: UP007


class FIOWebhookRootSchema(BaseModel):
    Data: list[FIOWebhookEndpointUnion]

    # disregard / remove undefined endpoints
    @field_validator('Data', mode='before')
    @classmethod
    def filter_unknown_endpoints(cls, v):
        if not isinstance(v, list):
            return [v]

        allowed_endpoints: set[str] = {'/cx'}

        return [item for item in v if isinstance(item, dict) and item.get('Endpoint') in allowed_endpoints]
