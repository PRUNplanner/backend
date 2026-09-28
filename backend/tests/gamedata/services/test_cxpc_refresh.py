from datetime import UTC, datetime

import pytest
from gamedata.fio.schemas import FIOExchangeFullSChema
from gamedata.services.cxpc_refresh import CXPCBranch, CXPCFetch, cxpc_window_start_ms, select_cxpc_pairs

DAY = 86_400_000
WINDOW = 1_790_294_400_000  # 2026-09-25 00:00 UTC
PAIR = ('RAT', 'NC1')


def _exchange(last_trade: int | None) -> FIOExchangeFullSChema:
    return FIOExchangeFullSChema.model_validate(
        {
            'MaterialTicker': 'RAT',
            'ExchangeCode': 'NC1',
            'PriceAverage': 1.0,
            'Traded': 0,
            'VolumeAmount': 0,
            'PriceTimeEpochMs': last_trade,
        }
    )


def test_window_start_is_three_days_before_midnight_utc() -> None:
    assert cxpc_window_start_ms(datetime(2026, 9, 28, 23, 59, tzinfo=UTC)) == WINDOW
    assert cxpc_window_start_ms(datetime(2026, 9, 28, 0, 0, tzinfo=UTC)) == WINDOW


@pytest.mark.parametrize(
    'cursor, last_trade, branch, since_ms',
    [
        # no rows
        (None, None, 'skip_never_traded', None),
        (None, WINDOW - 100 * DAY, 'backfill', None),
        # (a) candle in window, whatever the last trade says
        (WINDOW + DAY, None, 'in_window', WINDOW),
        (WINDOW + DAY, WINDOW - 10 * DAY, 'in_window', WINDOW),
        (WINDOW, None, 'in_window', WINDOW),
        # (b) traded in window
        (WINDOW - 10 * DAY, WINDOW, 'traded_in_window', WINDOW - 10 * DAY),
        (WINDOW - 10 * DAY, WINDOW + DAY, 'traded_in_window', WINDOW - 10 * DAY),
        # (c) late candle: cursor 7 days ago, trade 5 days ago
        (WINDOW - 4 * DAY, WINDOW - 2 * DAY, 'traded_since_cursor', WINDOW - 4 * DAY),
        (WINDOW - 4 * DAY, WINDOW - 3 * DAY, 'traded_since_cursor', WINDOW - 4 * DAY),
        (WINDOW - 4 * DAY, WINDOW - 1, 'traded_since_cursor', WINDOW - 4 * DAY),
        # stale; a trade on the cursor's own day is already in that candle
        (WINDOW - 1, None, 'skip_stale', WINDOW - 1),
        (WINDOW - 4 * DAY, WINDOW - 3 * DAY - 1, 'skip_stale', WINDOW - 4 * DAY),
        (WINDOW - 4 * DAY, WINDOW - 4 * DAY, 'skip_stale', WINDOW - 4 * DAY),
        (WINDOW - 4 * DAY, WINDOW - 4 * DAY - 1, 'skip_stale', WINDOW - 4 * DAY),
        (WINDOW - 4 * DAY, None, 'skip_stale', WINDOW - 4 * DAY),
    ],
)
def test_select_cxpc_pairs(
    cursor: int | None, last_trade: int | None, branch: CXPCBranch, since_ms: int | None
) -> None:
    cursors = {} if cursor is None else {PAIR: cursor}

    [fetch] = select_cxpc_pairs([_exchange(last_trade)], cursors, WINDOW)

    assert fetch == CXPCFetch('RAT', 'NC1', branch, since_ms)
    assert fetch.fetch is not branch.startswith('skip_')
