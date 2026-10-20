from collections import defaultdict
from decimal import Decimal
from typing import Literal

from django.db.models import Sum
from django.utils import timezone

from gamedata.models import GameExchangeCXPC

DAY_MS = 24 * 60 * 60 * 1000
PHYSICAL_EXCHANGES = ['AI1', 'CI1', 'IC1', 'NC1']

type SparkMetric = Literal['vwap_daily', 'vwap_7d']


def utc_today_epoch() -> int:
    """Today 00:00 UTC in epoch ms."""
    return int(timezone.now().timestamp() * 1000) // DAY_MS * DAY_MS


def build_sparklines(
    exchange_code: str,
    days: int,
    today_epoch: int,
    metric: SparkMetric = 'vwap_daily',
    tickers: list[str] | None = None,
) -> dict[str, list[float | None]]:
    """
    VWAP (volume / traded) per ticker for `days` days, oldest first, rounded to cents: daily up to `today_epoch`
    (UTC midnight, ms), or `vwap_7d` over the trailing 7 days up to yesterday, so every point is a complete day and
    the last one matches the market summary's `as_of`. UNIVERSE sums the physical exchanges, like the analytics
    view. A day without trades is None; tickers without any value in the window are left out, and `tickers` limits
    the result to those. One query.
    """
    # vwap_7d ends on the last complete day and reads 6 more days, so the first point has a full window
    lead = 6 if metric == 'vwap_7d' else 0
    end = today_epoch - DAY_MS if metric == 'vwap_7d' else today_epoch
    start = end - (days - 1 + lead) * DAY_MS
    exchanges = PHYSICAL_EXCHANGES if exchange_code == 'UNIVERSE' else [exchange_code]

    qs = GameExchangeCXPC.objects.filter(
        exchange_code__in=exchanges, date_epoch__gte=start, date_epoch__lt=end + DAY_MS
    )
    if tickers is not None:
        qs = qs.filter(ticker__in=tickers)
    rows = qs.values('ticker', 'date_epoch').annotate(volume_sum=Sum('volume'), traded_sum=Sum('traded'))

    # (volume, traded) per ticker and day; epochs that aren't midnight-aligned still land on their day
    totals: dict[str, dict[int, list[Decimal]]] = defaultdict(lambda: defaultdict(lambda: [Decimal(0), Decimal(0)]))
    for row in rows:
        day = totals[row['ticker']][(row['date_epoch'] - start) // DAY_MS]
        day[0] += row['volume_sum']
        day[1] += row['traded_sum']

    series: dict[str, list[float | None]] = {}
    for ticker, by_day in totals.items():
        points: list[float | None] = []
        for i in range(days):
            window = [by_day[j] for j in range(i, i + lead + 1) if j in by_day]
            volume, traded = sum((v for v, _ in window), Decimal(0)), sum((t for _, t in window), Decimal(0))
            points.append(round(float(volume / traded), 2) if traded else None)
        if any(point is not None for point in points):
            series[ticker] = points
    return series
