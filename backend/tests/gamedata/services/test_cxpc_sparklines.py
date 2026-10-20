from collections.abc import Callable

import pytest
from gamedata.services.cxpc_sparklines import DAY_MS, build_sparklines

pytestmark = pytest.mark.django_db

TODAY = 1_790_294_400_000  # 2026-09-25 00:00 UTC


def _day(days_ago: int) -> int:
    return TODAY - days_ago * DAY_MS


def test_pads_to_days_oldest_first(exchange_cxpc_factory: Callable[..., object]) -> None:
    exchange_cxpc_factory(ticker='RAT', exchange_code='AI1', date_epoch=_day(0), volume=300, traded=3)
    exchange_cxpc_factory(ticker='RAT', exchange_code='AI1', date_epoch=_day(29), volume=100, traded=1)

    series = build_sparklines('AI1', 30, TODAY)

    assert len(series['RAT']) == 30
    assert series['RAT'][0] == 100.0
    assert series['RAT'][-1] == 100.0
    assert series['RAT'][1:-1] == [None] * 28


def test_universe_sums_the_exchanges(exchange_cxpc_factory: Callable[..., object]) -> None:
    exchange_cxpc_factory(ticker='DW', exchange_code='AI1', date_epoch=_day(0), volume=100, traded=10)
    exchange_cxpc_factory(ticker='DW', exchange_code='NC1', date_epoch=_day(0), volume=300, traded=10)

    assert build_sparklines('UNIVERSE', 30, TODAY)['DW'][-1] == 20.0
    assert build_sparklines('NC1', 30, TODAY)['DW'][-1] == 30.0


def test_zero_traded_is_null_and_outside_rows_are_ignored(exchange_cxpc_factory: Callable[..., object]) -> None:
    exchange_cxpc_factory(ticker='H2O', exchange_code='CI1', date_epoch=_day(1), volume=0, traded=0)
    exchange_cxpc_factory(ticker='H2O', exchange_code='CI1', date_epoch=_day(0), volume=50, traded=5)
    exchange_cxpc_factory(ticker='FE', exchange_code='CI1', date_epoch=_day(30), volume=50, traded=5)
    exchange_cxpc_factory(ticker='FE', exchange_code='CI1', date_epoch=_day(-1), volume=50, traded=5)
    exchange_cxpc_factory(ticker='O', exchange_code='CI1', date_epoch=_day(2), volume=0, traded=0)

    series = build_sparklines('CI1', 30, TODAY)

    assert series['H2O'][-2:] == [None, 10.0]
    # FE only traded outside the window, O never: both left out
    assert set(series) == {'H2O'}


def test_rounds_to_cents(exchange_cxpc_factory: Callable[..., object]) -> None:
    exchange_cxpc_factory(ticker='RAT', exchange_code='AI1', date_epoch=_day(0), volume=100, traded=3)

    assert build_sparklines('AI1', 30, TODAY)['RAT'][-1] == 33.33


def test_one_query(exchange_cxpc_factory: Callable[..., object], django_assert_num_queries) -> None:
    for ticker in ['RAT', 'DW', 'FE']:
        for exchange in ['AI1', 'CI1', 'IC1', 'NC1']:
            exchange_cxpc_factory(ticker=ticker, exchange_code=exchange, date_epoch=_day(3), volume=10, traded=1)

    with django_assert_num_queries(1):
        assert len(build_sparklines('UNIVERSE', 30, TODAY)) == 3


def test_vwap_7d_rolls_over_the_trailing_week_up_to_yesterday(exchange_cxpc_factory: Callable[..., object]) -> None:
    # the window ends yesterday; 6 days before it still count for its first point
    exchange_cxpc_factory(ticker='RAT', exchange_code='AI1', date_epoch=_day(36), volume=100, traded=1)
    exchange_cxpc_factory(ticker='RAT', exchange_code='AI1', date_epoch=_day(4), volume=300, traded=1)
    exchange_cxpc_factory(ticker='RAT', exchange_code='AI1', date_epoch=_day(1), volume=100, traded=1)

    series = build_sparklines('AI1', 30, TODAY, 'vwap_7d')['RAT']

    assert len(series) == 30
    assert series[0] == 100.0
    assert series[1] is None
    # the last point is yesterday's VWAP 7D: days 7..1 ago
    assert series[-4:] == [300.0, 300.0, 300.0, 200.0]


def test_vwap_7d_ignores_today(exchange_cxpc_factory: Callable[..., object]) -> None:
    exchange_cxpc_factory(ticker='RAT', exchange_code='AI1', date_epoch=_day(1), volume=100, traded=1)
    before = build_sparklines('AI1', 7, TODAY, 'vwap_7d')

    exchange_cxpc_factory(ticker='RAT', exchange_code='AI1', date_epoch=_day(0), volume=900, traded=1)
    exchange_cxpc_factory(ticker='DW', exchange_code='AI1', date_epoch=_day(0), volume=900, traded=1)

    assert build_sparklines('AI1', 7, TODAY, 'vwap_7d') == before == {'RAT': [None] * 6 + [100.0]}


def test_tickers_limit_the_series(exchange_cxpc_factory: Callable[..., object]) -> None:
    for ticker in ['RAT', 'DW', 'FE']:
        exchange_cxpc_factory(ticker=ticker, exchange_code='AI1', date_epoch=_day(0), volume=10, traded=1)

    assert set(build_sparklines('AI1', 30, TODAY, tickers=['RAT', 'DW', 'XYZ'])) == {'RAT', 'DW'}
