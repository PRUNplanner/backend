from datetime import UTC, datetime, timedelta
from typing import Literal, NamedTuple

from gamedata.fio.schemas import FIOExchangeFullSChema

# FIO keeps sending a day's candle while the day is open and sometimes revises older ones,
# so every fetched pair re-upserts at least this many days
CXPC_WINDOW_DAYS = 3
DAY_MS = 86_400_000

type CXPCBranch = Literal[
    'backfill', 'in_window', 'traded_in_window', 'traded_since_cursor', 'skip_never_traded', 'skip_stale'
]


class CXPCFetch(NamedTuple):
    ticker: str
    exchange_code: str
    branch: CXPCBranch
    # None fetches the full history
    since_ms: int | None = None

    @property
    def fetch(self) -> bool:
        return not self.branch.startswith('skip_')


def cxpc_window_start_ms(now: datetime | None = None) -> int:
    """Today 00:00 UTC minus the window, in epoch ms."""
    midnight = (now or datetime.now(UTC)).astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((midnight - timedelta(days=CXPC_WINDOW_DAYS)).timestamp() * 1000)


def select_cxpc_pairs(
    exchanges: list[FIOExchangeFullSChema], cursors: dict[tuple[str, str], int], window_start: int
) -> list[CXPCFetch]:
    """One decision per pair for a regular refresh. cursors: newest stored candle per (ticker, exchange_code)."""
    plan: list[CXPCFetch] = []

    for ex in exchanges:
        cursor = cursors.get((ex.ticker, ex.exchange_code))
        last_trade = ex.price_time_epochms
        branch: CXPCBranch

        if cursor is None:
            branch = 'skip_never_traded' if last_trade is None else 'backfill'
            plan.append(CXPCFetch(ex.ticker, ex.exchange_code, branch))
            continue

        if cursor >= window_start:
            branch = 'in_window'
        elif last_trade is not None and last_trade >= window_start:
            branch = 'traded_in_window'
        # the cursor is the start of a candle day, a trade later that day is already in it
        elif last_trade is not None and last_trade >= cursor + DAY_MS:
            branch = 'traded_since_cursor'
        else:
            branch = 'skip_stale'

        plan.append(CXPCFetch(ex.ticker, ex.exchange_code, branch, min(cursor, window_start)))

    return plan
