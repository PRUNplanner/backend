from collections.abc import Callable

import pytest
from analytics.models import AnalyticsEmpireMaterialSnapshot, AnalyticsPlanAggregate
from gamedata.models.game_planet import GamePlanet
from model_bakery import baker
from planning.models import PlanningEmpire


@pytest.fixture()
def planet_factory() -> Callable[..., GamePlanet]:
    return lambda **kwargs: baker.make('gamedata.GamePlanet', make_m2m=True, **kwargs)


@pytest.fixture()
def plan_aggregate_factory() -> Callable[..., AnalyticsPlanAggregate]:
    return lambda **kwargs: baker.make('analytics.AnalyticsPlanAggregate', **kwargs)


@pytest.fixture()
def empire_factory() -> Callable[..., PlanningEmpire]:
    return lambda **kwargs: baker.make('planning.PlanningEmpire', **kwargs)


@pytest.fixture()
def material_snapshot_factory() -> Callable[..., AnalyticsEmpireMaterialSnapshot]:
    return lambda **kwargs: baker.make('analytics.AnalyticsEmpireMaterialSnapshot', **kwargs)
