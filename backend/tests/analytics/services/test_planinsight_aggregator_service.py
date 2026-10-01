from collections import Counter, defaultdict

import pytest
from analytics.models import AnalyticsPlanAggregate
from analytics.services.planinsight_aggregator_service import PlanInsightAggregatorService, _BuildingUsage
from django.utils import timezone
from model_bakery import baker
from planning.models import PlanningPlan
from user.models import User

pytestmark = pytest.mark.django_db

PLANET = 'KW-688c'

type Recipes = dict[str, int]
type Building = tuple[str, int, Recipes]


def _user() -> User:
    return baker.make('user.User', last_login=timezone.now())


def _plan(user: User, buildings: list[Building], experts: dict[str, int] | None = None) -> PlanningPlan:
    data = {
        'experts': [{'type': t, 'amount': a} for t, a in (experts or {}).items()],
        'workforce': [],
        'infrastructure': [],
        'buildings': [
            {
                'name': ticker,
                'amount': amount,
                'active_recipes': [{'recipeid': rid, 'amount': slots} for rid, slots in recipes.items()],
            }
            for ticker, amount, recipes in buildings
        ],
    }
    return baker.make('planning.PlanningPlan', user=user, planet_natural_id=PLANET, plan_data=data)


@pytest.fixture()
def gamedata() -> None:
    for ticker in ('FRM', 'PP1', 'BMP', 'RIG'):
        baker.make('gamedata.GameBuilding', building_id=ticker, building_ticker=ticker)
    for ticker, name in (('FRM', 'GRN'), ('FRM', 'BEA'), ('FRM', 'HER'), ('PP1', 'PE'), ('BMP', 'BSE')):
        baker.make(
            'gamedata.GameRecipe', standard_recipe_name=f'{ticker}#{name}', building_ticker=ticker, recipe_name=name
        )


def _snapshot_plans() -> None:
    """16 plans from 10 users: invalid building and recipe, extraction, experts, a building listed twice."""
    users = [_user() for _ in range(10)]
    for i in range(16):
        buildings: list[Building] = [
            ('FRM', 2 + i % 3, {'FRM#GRN': 1 + i % 2} | ({'FRM#BEA': 1} if i % 3 == 0 else {}))
        ]
        if i % 2 == 0:
            buildings.append(('PP1', 1, {'PP1#PE': 2}))
        if i % 4 == 0:
            buildings.append(('BMP', 3, {'BMP#BSE': 1, 'BMP#GONE': 1}))
        if i % 5 == 0:
            buildings.append(('XXX', 4, {'XXX#A': 1}))
        if i < 6:
            buildings.append(('RIG', 1, {'RIG#water': 1}))
        if i == 7:
            buildings.append(('FRM', 1, {'FRM#HER': 2}))
        _plan(users[i % 10], buildings, {'CHEMISTRY': i % 3, 'AGRICULTURE': 1 + i % 2})


class TestRecipeDistribution:
    def test_keys_recipes_by_their_building(self) -> None:
        service = PlanInsightAggregatorService()

        result = service._get_recipe_distribution({'BMP': Counter({'BMP#A=>B': 8, 'BMP#C=>D': 2})}, ['BMP'])

        assert result == {
            'BMP': [{'recipe_id': 'BMP#A=>B', 'percentage': 80.0}, {'recipe_id': 'BMP#C=>D', 'percentage': 20.0}]
        }

    def test_result_key_is_the_building_not_the_recipe_prefix(self) -> None:
        service = PlanInsightAggregatorService()

        result = service._get_recipe_distribution({'BMP': Counter({'PP1#A=>B': 10})}, ['BMP', 'PP1'])

        assert list(result) == ['BMP']


class TestV1Snapshot:
    def test_v1_keys_unchanged(self, gamedata: None) -> None:
        _snapshot_plans()

        PlanInsightAggregatorService().process_planet(PLANET)

        data = AnalyticsPlanAggregate.objects.get(planet_natural_id=PLANET).insights_data
        v1 = {k: data[k] for k in ('expert_distribution', 'building_distribution', 'recipe_distribution')}
        assert v1 == V1_SNAPSHOT


def _aggregate() -> dict:
    PlanInsightAggregatorService().aggregate_all_plans()
    return AnalyticsPlanAggregate.objects.get(planet_natural_id=PLANET).insights_data


def _building(data: dict, ticker: str) -> dict | None:
    return next((b for b in data['buildings'] if b['ticker'] == ticker), None)


class TestPlanetThresholds:
    def test_enough_plans_from_too_few_users_is_below_threshold(self, gamedata: None) -> None:
        users = [_user() for _ in range(9)]
        for i in range(20):
            _plan(users[i % 9], [('FRM', 1, {'FRM#GRN': 1})])
        baker.make('analytics.AnalyticsPlanAggregate', planet_natural_id=PLANET)  # stale row from an earlier run

        assert PlanInsightAggregatorService().aggregate_all_plans() == (0, 1)
        assert PlanInsightAggregatorService().process_planet(PLANET) is None
        assert not AnalyticsPlanAggregate.objects.exists()

    def test_ten_users_pass_and_total_users_is_stored(self, gamedata: None) -> None:
        users = [_user() for _ in range(10)]
        for i in range(15):
            _plan(users[i % 10], [('FRM', 1, {'FRM#GRN': 1})])

        assert PlanInsightAggregatorService().aggregate_all_plans() == (1, 0)
        aggregate = AnalyticsPlanAggregate.objects.get(planet_natural_id=PLANET)
        assert (aggregate.total_plans_analyzed, aggregate.total_users) == (15, 10)


class TestV2UserFloor:
    def test_items_of_nine_users_are_dropped_items_of_ten_kept(self, gamedata: None) -> None:
        users = [_user() for _ in range(10)]
        for i, user in enumerate(users):
            for _ in range(2):
                buildings: list[Building] = [('FRM', 2, {'FRM#GRN': 1} | ({'FRM#HER': 1} if i < 9 else {}))]
                if i < 9:
                    buildings.append(('PP1', 1, {'PP1#PE': 1}))
                _plan(user, buildings)

        data = _aggregate()

        assert _building(data, 'PP1') is None  # 90 % of plans, but 9 users
        frm = _building(data, 'FRM')
        assert frm is not None
        assert (frm['plans'], frm['users'], frm['percentage']) == (20, 10, 100.0)
        assert [r['recipe_id'] for r in frm['recipes']] == ['FRM#GRN']  # HER: 9 users
        assert frm['mixes'] == []  # {GRN, HER}: 9 users, {GRN}: 1 user

    def test_percentage_floor(self) -> None:
        service = PlanInsightAggregatorService()
        usage = defaultdict(_BuildingUsage)
        for user_id in range(10):
            usage['FRM'].amounts.append(1)
            usage['FRM'].users.add(user_id)

        assert [b['ticker'] for b in service._get_buildings_v2(usage, 200)] == ['FRM']  # 5 %
        assert service._get_buildings_v2(usage, 201) == []  # 4.98 %


class TestV2Mixes:
    def test_frm_mixes_amounts_and_medians(self, gamedata: None) -> None:
        users = [_user() for _ in range(10)]
        for i, user in enumerate(users):
            _plan(user, [('FRM', 3 + i % 2, {'FRM#GRN': 2, 'FRM#BEA': 1 + i % 3})], {'CHEMISTRY': 1 + i % 2})
            _plan(user, [('FRM', 6, {'FRM#GRN': 1, 'FRM#HER': 3})])

        frm = _building(_aggregate(), 'FRM')

        assert frm is not None
        assert (frm['plans'], frm['percentage'], frm['median_amount']) == (20, 100.0, 5)
        assert frm['recipes'] == [
            {'recipe_id': 'FRM#GRN', 'plans': 20, 'percentage': 100.0, 'median_amount': 2},  # 1.5 rounds up
            {'recipe_id': 'FRM#BEA', 'plans': 10, 'percentage': 50.0, 'median_amount': 2},
            {'recipe_id': 'FRM#HER', 'plans': 10, 'percentage': 50.0, 'median_amount': 3},
        ]
        assert frm['mixes'] == [
            {
                'recipe_ids': ['FRM#BEA', 'FRM#GRN'],
                'plans': 10,
                'percentage': 50.0,
                'median_building_amount': 4,  # median 3.5, half up
                'recipe_amounts': {'FRM#GRN': 2, 'FRM#BEA': 2},
            },
            {
                'recipe_ids': ['FRM#GRN', 'FRM#HER'],
                'plans': 10,
                'percentage': 50.0,
                'median_building_amount': 6,
                'recipe_amounts': {'FRM#GRN': 1, 'FRM#HER': 3},
            },
        ]

    def test_experts_raw_type_share_of_plans_and_median(self, gamedata: None) -> None:
        users = [_user() for _ in range(10)]
        for i, user in enumerate(users):
            _plan(user, [('FRM', 1, {'FRM#GRN': 1})], {'Chemistry': 1 + i % 2, 'Agriculture': 0})
            _plan(user, [('FRM', 1, {'FRM#GRN': 1})], {'Food_Industries': 4})

        assert _aggregate()['experts'] == [
            {'type': 'Chemistry', 'plans_percentage': 50.0, 'median_amount': 2},
            {'type': 'Food_Industries', 'plans_percentage': 50.0, 'median_amount': 4},
        ]

    def test_top_three_mixes(self, gamedata: None) -> None:
        mixes = [{'FRM#GRN': 1}, {'FRM#BEA': 1}, {'FRM#HER': 1}, {'FRM#GRN': 1, 'FRM#BEA': 1}]
        users = [_user() for _ in range(10)]
        for user in users:
            for recipes in mixes:
                _plan(user, [('FRM', 1, recipes)])

        frm = _building(_aggregate(), 'FRM')

        assert frm is not None
        assert len(frm['mixes']) == 3


# captured from the v1-only aggregator (main before insights-01a) on _snapshot_plans()
V1_SNAPSHOT = {
    'expert_distribution': [{'type': 'Agriculture', 'percentage': 61.54}, {'type': 'Chemistry', 'percentage': 38.46}],
    'building_distribution': [
        {'ticker': 'FRM', 'percentage': 100.0},
        {'ticker': 'PP1', 'percentage': 50.0},
        {'ticker': 'RIG', 'percentage': 37.5},
        {'ticker': 'BMP', 'percentage': 25.0},
    ],
    'recipe_distribution': {
        'FRM': [{'recipe_id': 'FRM#GRN', 'percentage': 69.57}, {'recipe_id': 'FRM#BEA', 'percentage': 26.09}],
        'PP1': [{'recipe_id': 'PP1#PE', 'percentage': 100.0}],
        'BMP': [{'recipe_id': 'BMP#BSE', 'percentage': 100.0}],
        'RIG': [{'recipe_id': 'RIG#water', 'percentage': 100.0}],
    },
}
