from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum

from gamedata.models import GameExchangeCXPC

DAY_MS = 24 * 60 * 60 * 1000
PHYSICAL_EXCHANGES = ['AI1', 'CI1', 'IC1', 'NC1']


def build_sparklines(exchange_code: str, days: int, today_epoch: int) -> dict[str, list[float | None]]:
    """
    Daily VWAP (volume / traded) per ticker for the `days` days up to `today_epoch` (UTC midnight, ms), oldest
    first, rounded to cents. UNIVERSE sums the physical exchanges, like the analytics view. A day without trades
    is None; tickers without any trade in the window are left out. One query.
    """
    start = today_epoch - (days - 1) * DAY_MS
    exchanges = PHYSICAL_EXCHANGES if exchange_code == 'UNIVERSE' else [exchange_code]

    rows = (
        GameExchangeCXPC.objects.filter(
            exchange_code__in=exchanges, date_epoch__gte=start, date_epoch__lt=today_epoch + DAY_MS
        )
        .values('ticker', 'date_epoch')
        .annotate(volume_sum=Sum('volume'), traded_sum=Sum('traded'))
    )

    # (volume, traded) per ticker and day; epochs that aren't midnight-aligned still land on their day
    totals: dict[str, dict[int, list[Decimal]]] = defaultdict(lambda: defaultdict(lambda: [Decimal(0), Decimal(0)]))
    for row in rows:
        day = totals[row['ticker']][(row['date_epoch'] - start) // DAY_MS]
        day[0] += row['volume_sum']
        day[1] += row['traded_sum']

    series: dict[str, list[float | None]] = {}
    for ticker, by_day in totals.items():
        if not any(traded for _, traded in by_day.values()):
            continue
        series[ticker] = [
            round(float(by_day[i][0] / by_day[i][1]), 2) if i in by_day and by_day[i][1] else None for i in range(days)
        ]
    return series
