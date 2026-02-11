from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CX_TYPES = Literal['BUY', 'SELL', 'BOTH']

CX_EXCHANGES = Literal[
    'AI1_7D', 'NC1_7D', 'CI1_7D', 'IC1_7D', 'UNIVERSE_7D', 'AI1_30D', 'NC1_30D', 'CI1_30D', 'IC1_30D', 'UNIVERSE_30D'
]


# LEGACY


class CXTickerPreference_Legacy(BaseModel):
    ticker: str
    type: CX_TYPES
    value: float


class CXExchangePreference_Legacy(BaseModel):
    type: CX_TYPES
    exchange: CX_EXCHANGES | str

    @field_validator('exchange', mode='before')
    @classmethod
    def remap_exchanges(cls, v: str) -> str:
        if not isinstance(v, str):
            return v

        # PP pattern (PP7D_AI1 -> AI1_7D)#
        if v.startswith('PP'):
            # ['PP7D', 'SYMBOL'] or ['PP30D', 'SYMBOL']
            parts = v.split('_')
            period = parts[0].replace('PP', '')
            symbol = parts[1].replace('2', '1')  # only do AI1, NC1, CI1 or IC1
            return f'{symbol}_{period}'

        prefixes = ['AI1', 'NC1', 'CI1', 'IC1', 'NC2', 'CI2']
        for p in prefixes:
            if v.startswith(p):
                normalized_p = p.replace('2', '1')
                return f'{normalized_p}_7D'

        return v


class CXExchangeTickerPreferences_Legacy(BaseModel):
    model_config = ConfigDict(extra='ignore')

    class CXEchangePlanetPreference_Legacy(BaseModel):
        planet: str
        preferences: list[CXExchangePreference_Legacy]

    class CXTickerPlanetPreference_Legacy(BaseModel):
        planet: str
        preferences: list[CXTickerPreference_Legacy]

    cx_empire: list[CXExchangePreference_Legacy]
    cx_planets: list[CXEchangePlanetPreference_Legacy]
    ticker_empire: list[CXTickerPreference_Legacy]
    ticker_planets: list[CXTickerPlanetPreference_Legacy]


# V1


class CXTickerPreference_V1(BaseModel):
    ticker: str
    type: CX_TYPES
    value: float


class CXExchangePreference_V1(BaseModel):
    type: CX_TYPES
    exchange: str


class CXExchangeTickerPreferences_V1(BaseModel):
    class CXEchangePlanetPreference_V1(BaseModel):
        planet: str
        preferences: list[CXExchangePreference_V1]

    class CXTickerPlanetPreference_V1(BaseModel):
        planet: str
        preferences: list[CXTickerPreference_V1]

    cx_empire: list[CXExchangePreference_V1] = Field(default_factory=list)
    cx_planets: list[CXEchangePlanetPreference_V1] = Field(default_factory=list)
    ticker_empire: list[CXTickerPreference_V1] = Field(default_factory=list)
    ticker_planets: list[CXTickerPlanetPreference_V1] = Field(default_factory=list)

    @model_validator(mode='after')
    def remove_empty_planet_preferences(self) -> 'CXExchangeTickerPreferences_V1':
        # filter out planet entries with empty preference lists
        self.cx_planets = [p for p in self.cx_planets if p.preferences]
        self.ticker_planets = [p for p in self.ticker_planets if p.preferences]
        return self
