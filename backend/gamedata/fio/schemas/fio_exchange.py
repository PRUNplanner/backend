from typing import Literal

from pydantic import BaseModel, Field, computed_field

EXCHANGE_CODES = Literal['AI1', 'NC1', 'CI1', 'IC1', 'NC2', 'CI2']

CXPC_INTERVALS = Literal[
    'DAY_ONE',
    'DAY_THREE',
    'HOUR_ONE',
    'HOUR_TWO',
    'HOUR_FOUR',
    'HOUR_SIX',
    'HOUR_TWELVE',
    'MINUTE_FIVE',
    'MINUTE_FIFTEEN',
    'MINUTE_THIRTY',
]


class FIOExchangeSchema(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=3, alias='MaterialTicker')
    exchange_code: EXCHANGE_CODES = Field(alias='ExchangeCode')

    @computed_field
    @property
    def ticker_id(self) -> str:
        return f'{self.ticker}.{self.exchange_code}'

    mm_buy: float | None = Field(None, ge=0.0, alias='MMBuy')
    mm_sell: float | None = Field(None, ge=0.0, alias='MMSell')

    price_average: float = Field(..., ge=0.0, alias='PriceAverage')
    ask: float | None = Field(None, ge=0.0, alias='Ask')
    bid: float | None = Field(None, ge=0.0, alias='Bid')

    ask_count: int | None = Field(None, ge=0, alias='AskCount')
    bid_count: int | None = Field(None, ge=0, alias='BidCount')
    supply: int | None = Field(None, ge=0, alias='Supply')
    demand: int | None = Field(None, ge=0, alias='Demand')


class FIOExchangeFullSChema(FIOExchangeSchema):
    # additional fields
    last_price: float | None = Field(None, ge=0.0, alias='Price')
    traded: int = Field(ge=0, alias='Traded')
    volume_amount: float = Field(ge=0, alias='VolumeAmount')


class FIOExchangeCXPC(BaseModel):
    interval: CXPC_INTERVALS = Field(alias='Interval')
    date_epoch: int = Field(ge=0, alias='DateEpochMs')
    open: float = Field(ge=0.0, alias='Open')
    close: float = Field(ge=0.0, alias='Close')
    high: float = Field(ge=0.0, alias='High')
    low: float = Field(ge=0.0, alias='Low')
    volume: float = Field(ge=0.0, alias='Volume')
    traded: int = Field(ge=0, alias='Traded')
