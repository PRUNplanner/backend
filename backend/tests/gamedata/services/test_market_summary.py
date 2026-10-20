from collections.abc import Callable
from decimal import Decimal

import pytest
from gamedata.services.cxpc_sparklines import DAY_MS
from gamedata.services.market_sectors import FALLBACK_SECTOR, SECTORS
from gamedata.services.market_summary import (
    DAYS,
    HISTORY,
    MarketMaterial,
    MarketSummary,
    build_market_summary,
    cap_weights,
)
from model_bakery import baker

pytestmark = pytest.mark.django_db

TODAY = 1_790_294_400_000  # 2026-09-25 00:00 UTC
AS_OF = TODAY - DAY_MS


def _day(days_before_as_of: int) -> int:
    return AS_OF - days_before_as_of * DAY_MS


def _daily(ticker: str, exchange: str, prices: list[float], units: int = 10) -> None:
    """One candle per day ending on as_of, oldest first."""
    n = len(prices)
    baker.make(
        'gamedata.GameExchangeCXPC',
        ticker=ticker,
        exchange_code=exchange,
        date_epoch=iter([_day(n - 1 - i) for i in range(n)]),
        volume=iter([Decimal(str(round(price * units, 4))) for price in prices]),
        traded=units,
        _quantity=n,
        _bulk_create=True,
    )


def _material(summary: MarketSummary, ticker: str) -> MarketMaterial:
    return next(row for row in summary['materials'] if row['ticker'] == ticker)


class TestWindows:
    def test_vwaps_turnovers_and_units_match_a_hand_calculation(
        self, exchange_cxpc_factory: Callable[..., object], material_factory: Callable[..., object]
    ) -> None:
        material_factory(ticker='RAT', name='rations', category_name='consumables (basic)')
        exchange_cxpc_factory(ticker='RAT', exchange_code='AI1', date_epoch=_day(0), volume=1000, traded=10)
        exchange_cxpc_factory(ticker='RAT', exchange_code='AI1', date_epoch=_day(3), volume=1200, traded=10)
        exchange_cxpc_factory(ticker='RAT', exchange_code='NC1', date_epoch=_day(1), volume=500, traded=5)
        exchange_cxpc_factory(ticker='RAT', exchange_code='AI1', date_epoch=_day(10), volume=900, traded=10)

        row = _material(build_market_summary(TODAY), 'RAT')

        # 7 days: (1000 + 1200 + 500) / 25; 30 days: + 900 / 10
        assert row['vwap_7d'] == 108.0
        assert row['vwap_30d'] == 102.86
        assert row['change_pct'] == 5.0
        assert (row['turnover_30d'], row['turnover_7d'], row['units_7d']) == (3600, 2700, 25)
        assert row['turnover_7d_by_exchange'] == {'AI1': 2200, 'NC1': 500}
        assert (row['name'], row['category'], row['sector']) == ('rations', 'consumables (basic)', 'consumables')
        assert len(row['spark']) == 15
        assert row['spark'][-1] == 108.0

    def test_rows_keep_significant_digits(self) -> None:
        _daily('AIR', 'AI1', [200_023.77] * 7)
        _daily('RAT', 'AI1', [151.06] * 7)
        _daily('H2O', 'AI1', [0.12346] * 7)

        summary = build_market_summary(TODAY)

        assert _material(summary, 'AIR')['spark'][-1] == 200_000
        assert _material(summary, 'AIR')['vwap_7d'] == 200_023.77
        assert _material(summary, 'RAT')['spark'][-1] == 151.1
        assert _material(summary, 'H2O')['spark'][-1] == 0.1235
        # 7 days × 10 units × 151.06 = 10,574.2 ȼ
        rat = _material(summary, 'RAT')
        assert (rat['turnover_7d'], rat['turnover_30d']) == (10_570, 10_570)
        assert rat['turnover_7d_by_exchange'] == {'AI1': 10_600}
        assert _material(summary, 'H2O')['turnover_7d'] == 9

    def test_quiet_week_is_null_and_untraded_month_is_left_out(
        self, exchange_cxpc_factory: Callable[..., object]
    ) -> None:
        exchange_cxpc_factory(ticker='DW', exchange_code='CI1', date_epoch=_day(10), volume=100, traded=10)
        exchange_cxpc_factory(ticker='FE', exchange_code='CI1', date_epoch=_day(40), volume=100, traded=10)

        summary = build_market_summary(TODAY)

        dw = _material(summary, 'DW')
        assert (dw['vwap_7d'], dw['change_pct'], dw['turnover_7d'], dw['vwap_30d']) == (None, None, 0, 10.0)
        assert dw['turnover_7d_by_exchange'] == {}
        assert dw['name'] == '' and dw['sector'] == FALLBACK_SECTOR
        assert [row['ticker'] for row in summary['materials']] == ['DW']

    def test_todays_open_candle_never_changes_the_response(self, exchange_cxpc_factory: Callable[..., object]) -> None:
        exchange_cxpc_factory(ticker='RAT', exchange_code='AI1', date_epoch=_day(0), volume=1000, traded=10)
        before = build_market_summary(TODAY)

        exchange_cxpc_factory(ticker='RAT', exchange_code='AI1', date_epoch=TODAY, volume=99_999, traded=1)

        assert build_market_summary(TODAY) == before
        assert before['as_of'] == AS_OF
        assert before['start'] == AS_OF - 89 * DAY_MS
        assert before['days'] == DAYS

    def test_turnover_daily_per_exchange(self, exchange_cxpc_factory: Callable[..., object]) -> None:
        exchange_cxpc_factory(ticker='RAT', exchange_code='AI1', date_epoch=_day(0), volume=1000, traded=10)
        exchange_cxpc_factory(ticker='DW', exchange_code='AI1', date_epoch=_day(0), volume=250, traded=5)
        exchange_cxpc_factory(ticker='DW', exchange_code='AI1', date_epoch=_day(1), volume=0, traded=0)

        daily = build_market_summary(TODAY)['turnover_daily']

        assert set(daily) == {'AI1', 'CI1', 'IC1', 'NC1'}
        assert len(daily['AI1']) == DAYS
        assert daily['AI1'][-2:] == [None, 1250]
        assert daily['NC1'] == [None] * DAYS

    def test_at_most_three_queries(
        self, exchange_cxpc_factory: Callable[..., object], django_assert_max_num_queries
    ) -> None:
        for ticker in ['RAT', 'DW', 'FE']:
            for exchange in ['AI1', 'CI1', 'IC1', 'NC1']:
                exchange_cxpc_factory(ticker=ticker, exchange_code=exchange, date_epoch=_day(3), volume=10, traded=1)

        with django_assert_max_num_queries(3):
            build_market_summary(TODAY)


class TestIndex:
    @pytest.fixture(autouse=True)
    def _market(self, material_factory: Callable[..., object], exchange_cxpc_factory: Callable[..., object]) -> None:
        material_factory(ticker='FE', category_name='metals')
        material_factory(ticker='RAT', category_name='consumables (basic)')
        # FE on AI1 holds 100; RAT on NC1 doubles to 200 for the last week; H2O trades only on as_of
        _daily('FE', 'AI1', [100.0] * HISTORY)
        _daily('RAT', 'NC1', [100.0] * (HISTORY - 7) + [200.0] * 7)
        exchange_cxpc_factory(ticker='H2O', exchange_code='AI1', date_epoch=_day(0), volume=1000, traded=10)

    def test_starts_at_100_and_follows_its_members(self) -> None:
        index = build_market_summary(TODAY)['index']

        combined = index['combined']
        assert len(combined) == DAYS
        assert combined[0] == 100.0
        # two members can't stay under 5 %: equal weights
        assert combined[-1] == 150.0
        assert index['base'] == AS_OF - 89 * DAY_MS

    def test_members_trade_every_day_per_scope(self) -> None:
        index = build_market_summary(TODAY)['index']

        assert index['members'] == {
            'ALL': 2,
            'AI1': 1,
            'CI1': 0,
            'IC1': 0,
            'NC1': 1,
            **{key: 0 for key, _ in SECTORS},
            'consumables': 1,
            'metals_alloys': 1,
        }
        per_exchange = index['per_exchange']
        assert per_exchange['AI1'][-1] == 100.0
        assert per_exchange['NC1'][-1] == 200.0
        assert per_exchange['CI1'] == [None] * DAYS
        assert index['per_sector']['consumables'][-1] == 200.0
        # H2O's 1,000 on AI1 isn't held by a member
        fe_30d = 30 * 1000
        assert index['coverage']['AI1'] == round(fe_30d / (fe_30d + 1000), 3)
        assert index['coverage']['CI1'] == 0.0


class TestCapWeights:
    @pytest.mark.parametrize(
        'turnover',
        [
            {'T0': 1000.0, **{f'T{i}': 1.0 for i in range(1, 25)}},
            # capping the first pushes the second over the cap
            {'T0': 100.0, 'T1': 50.0, **{f'T{i}': 1.0 for i in range(2, 30)}},
        ],
    )
    def test_no_weight_over_the_cap_and_they_sum_to_one(self, turnover: dict[str, float]) -> None:
        weights = cap_weights(turnover, 0.05)

        assert sum(weights.values()) == pytest.approx(1)
        assert max(weights.values()) <= 0.05 + 1e-12
        assert weights['T0'] == pytest.approx(0.05)
        assert weights['T1'] <= weights['T0']

    def test_small_turnover_keeps_its_share(self) -> None:
        turnover = {f'T{i}': 1.0 for i in range(40)}

        assert cap_weights(turnover, 0.05) == pytest.approx(dict.fromkeys(turnover, 1 / 40))

    def test_too_few_members_for_the_cap_get_equal_weights(self) -> None:
        assert cap_weights({'A': 9.0, 'B': 1.0}, 0.2) == {'A': 0.5, 'B': 0.5}
        assert cap_weights({}, 0.05) == {}


class TestIsFlat:
    @pytest.mark.parametrize(
        ('prices', 'expected'),
        [
            ([200_000.0] * 30, True),
            ([100.0 * 1.02**day for day in range(30)], False),
            ([100.0] * 9, False),
            # 2 of 10 traded days off the band is still 80 % on it
            ([100.0] * 8 + [110.0, 90.0], True),
            ([100.0] * 7 + [110.0, 90.0, 120.0], False),
        ],
    )
    def test_flat_rule(self, prices: list[float], expected: bool) -> None:
        _daily('AIR', 'AI1', prices)

        assert _material(build_market_summary(TODAY), 'AIR')['is_flat'] is expected


def test_summary_lists_the_sectors() -> None:
    sectors = build_market_summary(TODAY)['sectors']

    assert [sector['key'] for sector in sectors] == [key for key, _ in SECTORS]
