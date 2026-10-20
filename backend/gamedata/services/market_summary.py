import math
from collections import defaultdict
from statistics import median
from typing import TypedDict, cast

from gamedata.models import GameExchangeCXPC, GameMaterial
from gamedata.services.cxpc_sparklines import DAY_MS, PHYSICAL_EXCHANGES
from gamedata.services.market_sectors import SECTORS, sector_of

MARKET_SUMMARY_ENDPOINT = 'market-summary'
ALL = 'ALL'
DAYS = 90
# day 0's VWAP 30D reaches 29 days further back
HISTORY = DAYS + 29
SPARK_DAYS = 15
INDEX_CAP = 0.05
SECTOR_INDEX_CAP = 0.2
FLAT_BAND = 0.003
FLAT_SHARE = 0.8
FLAT_MIN_DAYS = 10

type Series = list[float | None]


class MarketMaterial(TypedDict):
    ticker: str
    name: str
    category: str
    sector: str
    turnover_30d: float
    turnover_7d: float
    units_7d: int
    vwap_7d: float | None
    vwap_30d: float | None
    change_pct: float | None
    spark: Series
    is_flat: bool
    turnover_7d_by_exchange: dict[str, float]


class MarketIndex(TypedDict):
    base: int
    combined: Series
    per_exchange: dict[str, Series]
    per_sector: dict[str, Series]
    members: dict[str, int]
    coverage: dict[str, float]


class MarketSector(TypedDict):
    key: str
    categories: list[str]


class MarketSummary(TypedDict):
    as_of: int
    start: int
    days: int
    turnover_daily: dict[str, Series]
    index: MarketIndex
    sectors: list[MarketSector]
    materials: list[MarketMaterial]


class _Pair:
    """Daily volume (ȼ) and traded units of one ticker in one scope over HISTORY days, with prefix sums."""

    def __init__(self) -> None:
        self.volume = [0.0] * HISTORY
        self.traded = [0.0] * HISTORY
        self._pv: list[float] = []
        self._pt: list[float] = []

    def close(self) -> None:
        self._pv, self._pt = [0.0], [0.0]
        for v, t in zip(self.volume, self.traded, strict=True):
            self._pv.append(self._pv[-1] + v)
            self._pt.append(self._pt[-1] + t)

    def sums(self, day: int, window: int) -> tuple[float, float]:
        """Σ volume and Σ traded over the `window` days ending on `day` (an index into HISTORY)."""
        lo = max(0, day + 1 - window)
        return self._pv[day + 1] - self._pv[lo], self._pt[day + 1] - self._pt[lo]

    def vwap(self, day: int, window: int) -> float | None:
        volume, traded = self.sums(day, window)
        return volume / traded if traded else None

    def vwap_7d_series(self) -> Series:
        return [self.vwap(day, 7) for day in range(HISTORY - DAYS, HISTORY)]


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def _sig(value: float, digits: int) -> float:
    return round(value, digits - 1 - math.floor(math.log10(abs(value)))) if value else value


# Significant digits of the material rows, which are most of the payload: a sparkline shows a shape and turnover
# feeds shares (weights, treemap, exchange split), so cents on 200,000 ȼ or the last ȼ of 1 B ȼ only cost bytes.
SPARK_DIGITS = 4
TURNOVER_DIGITS = 4
EXCHANGE_TURNOVER_DIGITS = 3


def _spark_point(value: float | None) -> float | None:
    return None if value is None else _sig(value, SPARK_DIGITS)


def _turnover(value: float, digits: int) -> int:
    """Whole ȼ with at most `digits` significant digits."""
    return round(_sig(value, digits))


def cap_weights(turnover: dict[str, float], cap: float) -> dict[str, float]:
    """
    Turnover shares capped at `cap`, the excess redistributed over the uncapped members by turnover until none is
    over the cap. Fewer than 1 / cap members can't stay under it: they get equal weights.
    """
    if not turnover:
        return {}
    if len(turnover) * cap <= 1:
        return dict.fromkeys(turnover, 1 / len(turnover))

    total = sum(turnover.values())
    weights = {ticker: value / total for ticker, value in turnover.items()}
    capped: set[str] = set()
    while over := {ticker for ticker, weight in weights.items() if weight > cap and ticker not in capped}:
        capped |= over
        free = 1 - cap * len(capped)
        rest = sum(value for ticker, value in turnover.items() if ticker not in capped)
        weights = {ticker: cap if ticker in capped else free * turnover[ticker] / rest for ticker in turnover}
    return weights


def _index(prices: dict[str, Series], turnover: dict[str, float], cap: float) -> tuple[Series, int, float]:
    """(index per day, member count, members' share of the scope's 30-day turnover)."""
    # members have a VWAP 7D on every day; `all` also drops a zero price, which can't be a base
    members = {ticker: cast(list[float], series) for ticker, series in prices.items() if all(series)}
    weights = cap_weights({ticker: turnover[ticker] for ticker in members}, cap)
    total = sum(turnover.values())
    coverage = round(sum(turnover[ticker] for ticker in members) / total, 3) if total else 0.0
    if not members:
        return [None] * DAYS, 0, coverage

    index: Series = [
        round(100 * sum(w * members[t][d] / members[t][0] for t, w in weights.items()), 1) for d in range(DAYS)
    ]
    return index, len(members), coverage


def _is_flat(pair: _Pair) -> bool:
    """The daily VWAP sits within ±0.3 % of its 30-day median on at least 80 % of at least 10 traded days."""
    daily = [v / t for v, t in zip(pair.volume[-30:], pair.traded[-30:], strict=True) if t]
    if len(daily) < FLAT_MIN_DAYS:
        return False
    mid = median(daily)
    return sum(abs(price / mid - 1) <= FLAT_BAND for price in daily) >= FLAT_SHARE * len(daily)


def build_market_summary(today_epoch: int) -> MarketSummary:
    """
    The public market report over the 90 complete UTC days before `today_epoch` (UTC midnight, ms): turnover per
    exchange and day, turnover-weighted VWAP 7D indices and one row per ticker traded in the last 30 days. Money is
    in ȼ, the four currencies summed at par; ALL is the sum of the physical exchanges. Two queries.
    """
    as_of = today_epoch - DAY_MS
    first = as_of - (HISTORY - 1) * DAY_MS
    scopes = [ALL, *PHYSICAL_EXCHANGES]

    pairs: dict[str, dict[str, _Pair]] = defaultdict(lambda: {scope: _Pair() for scope in scopes})
    # today's open candle is left out, so the response holds still until the day is over
    rows = GameExchangeCXPC.objects.filter(
        exchange_code__in=PHYSICAL_EXCHANGES, date_epoch__gte=first, date_epoch__lt=today_epoch
    ).values_list('ticker', 'exchange_code', 'date_epoch', 'volume', 'traded')
    for ticker, exchange, epoch, volume, traded in rows:
        # epochs that aren't midnight-aligned still land on their day
        day = (epoch - first) // DAY_MS
        for scope in (ALL, exchange):
            pairs[ticker][scope].volume[day] += float(volume)
            pairs[ticker][scope].traded[day] += float(traded)
    for by_scope in pairs.values():
        for pair in by_scope.values():
            pair.close()

    last = HISTORY - 1
    names: dict[str, str] = {}
    categories: dict[str, str] = {}
    for ticker, name, category in GameMaterial.objects.values_list('ticker', 'name', 'category_name'):
        names[ticker], categories[ticker] = name, category
    sectors = {ticker: sector_of(categories.get(ticker, '')) for ticker in pairs}

    turnover_daily: dict[str, Series] = {}
    for exchange in PHYSICAL_EXCHANGES:
        turnover_daily[exchange] = [
            round(sum(p[exchange].volume[d] for p in pairs.values()))
            if any(p[exchange].traded[d] for p in pairs.values())
            else None
            for d in range(HISTORY - DAYS, HISTORY)
        ]

    prices = {scope: {t: p[scope].vwap_7d_series() for t, p in pairs.items()} for scope in scopes}
    turnover_30d = {scope: {t: p[scope].sums(last, 30)[0] for t, p in pairs.items()} for scope in scopes}

    per_scope = {scope: _index(prices[scope], turnover_30d[scope], INDEX_CAP) for scope in scopes}
    per_sector = {
        key: _index(
            {t: s for t, s in prices[ALL].items() if sectors[t] == key},
            {t: v for t, v in turnover_30d[ALL].items() if sectors[t] == key},
            SECTOR_INDEX_CAP,
        )
        for key, _ in SECTORS
    }

    materials: list[MarketMaterial] = []
    for ticker, by_scope in pairs.items():
        pair = by_scope[ALL]
        volume_30d, traded_30d = pair.sums(last, 30)
        if not traded_30d:
            continue
        volume_7d, traded_7d = pair.sums(last, 7)
        vwap_7d, vwap_30d = pair.vwap(last, 7), pair.vwap(last, 30)
        materials.append(
            {
                'ticker': ticker,
                'name': names.get(ticker, ''),
                'category': categories.get(ticker, ''),
                'sector': sectors[ticker],
                'turnover_30d': _turnover(volume_30d, TURNOVER_DIGITS),
                'turnover_7d': _turnover(volume_7d, TURNOVER_DIGITS),
                'units_7d': round(traded_7d),
                'vwap_7d': _round(vwap_7d, 2),
                'vwap_30d': _round(vwap_30d, 2),
                'change_pct': round((vwap_7d / vwap_30d - 1) * 100, 2) if vwap_7d and vwap_30d else None,
                'spark': [_spark_point(v) for v in prices[ALL][ticker][-SPARK_DAYS:]],
                'is_flat': _is_flat(pair),
                'turnover_7d_by_exchange': {
                    exchange: _turnover(volume, EXCHANGE_TURNOVER_DIGITS)
                    for exchange in PHYSICAL_EXCHANGES
                    if (volume := by_scope[exchange].sums(last, 7)[0])
                },
            }
        )
    materials.sort(key=lambda row: (-row['turnover_30d'], row['ticker']))

    return {
        'as_of': as_of,
        'start': as_of - (DAYS - 1) * DAY_MS,
        'days': DAYS,
        'turnover_daily': turnover_daily,
        'index': {
            'base': as_of - (DAYS - 1) * DAY_MS,
            'combined': per_scope[ALL][0],
            'per_exchange': {exchange: per_scope[exchange][0] for exchange in PHYSICAL_EXCHANGES},
            'per_sector': {key: index for key, (index, _, _) in per_sector.items()},
            'members': {scope: n for scope, (_, n, _) in (per_scope | per_sector).items()},
            'coverage': {scope: coverage for scope, (_, _, coverage) in per_scope.items()},
        },
        'sectors': [{'key': key, 'categories': categories_} for key, categories_ in SECTORS],
        'materials': materials,
    }
