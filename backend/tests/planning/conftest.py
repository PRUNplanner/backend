import pytest
from model_bakery import baker


@pytest.fixture()
def plan_factory(**kwargs):
    return lambda **kwargs: baker.make('planning.PlanningPlan', **kwargs)


@pytest.fixture()
def shared_factory(**kwargs):
    return lambda **kwargs: baker.make('planning.PlanningShared', **kwargs)


@pytest.fixture()
def cx_factory(**kwargs):
    return lambda **kwargs: baker.make('planning.PlanningCX', **kwargs)


@pytest.fixture()
def empire_factory(**kwargs):
    return lambda **kwargs: baker.make('planning.PlanningEmpire', **kwargs)
