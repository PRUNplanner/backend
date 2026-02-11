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
