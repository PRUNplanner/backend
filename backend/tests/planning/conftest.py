import pytest
from model_bakery import baker


@pytest.fixture()
def plan_factory(**kwargs):
    return lambda **kwargs: baker.make('planning.PlanningPlan', **kwargs)


@pytest.fixture()
def shared_factory(**kwargs):
    return lambda **kwargs: baker.make('planning.PlanningShared', **kwargs)
