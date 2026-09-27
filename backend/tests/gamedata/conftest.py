import pytest
from model_bakery import baker


@pytest.fixture()
def recipe_factory(**kwargs):
    return lambda **kwargs: baker.make('gamedata.GameRecipe', make_m2m=True, **kwargs)


@pytest.fixture()
def material_factory(**kwargs):
    return lambda **kwargs: baker.make('gamedata.GameMaterial', **kwargs)


@pytest.fixture()
def building_factory(**kwargs):
    return lambda **kwargs: baker.make('gamedata.GameBuilding', **kwargs)


@pytest.fixture()
def planet_factory(**kwargs):
    return lambda **kwargs: baker.make('gamedata.GamePlanet', make_m2m=True, **kwargs)


@pytest.fixture()
def building_cost_factory(**kwargs):
    return lambda **kwargs: baker.make('gamedata.GameBuildingCost', **kwargs)


@pytest.fixture()
def popr_factory(**kwargs):
    return lambda **kwargs: baker.make('gamedata.GamePlanetInfrastructureReport', **kwargs)


@pytest.fixture()
def production_fee_factory(**kwargs):
    return lambda **kwargs: baker.make('gamedata.GamePlanetProductionFee', **kwargs)


@pytest.fixture()
def exchange_analytics_factory():

    return lambda **kwargs: baker.make('gamedata.GameExchangeAnalytics', **kwargs)


@pytest.fixture()
def fio_playerdata_factory(**kwargs):
    return lambda **kwargs: baker.make('gamedata.GameFIOPlayerData', schema_version=1, **kwargs)


@pytest.fixture()
def exchange_cxpc_factory(**kwargs):
    return lambda **kwargs: baker.make('gamedata.GameExchangeCXPC', **kwargs)
