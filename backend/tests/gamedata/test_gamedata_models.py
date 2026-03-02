from uuid import uuid4

import pytest
from gamedata.models import GameBuilding, GameBuildingCost

pytestmark = pytest.mark.django_db


def test_model_gamebuilding(building_factory):

    building_factory(building_ticker='HBB', building_name='Foo')

    building = GameBuilding.objects.get(building_ticker='HBB')

    assert building is not None
    assert str(building) == 'HBB (Foo)'
    assert building.habitations is not None
    assert 'area' not in building.habitations

    required_keys = ['pioneers', 'settlers', 'technicians', 'engineers', 'scientists']
    for key in required_keys:
        assert key in building.habitations, f"Expected key '{key}' was not found in habitations"


def test_model_gamebuildingcost(building_factory, building_cost_factory):

    building_factory(building_ticker='HBB', building_name='Foo')
    building = GameBuilding.objects.get(building_ticker='HBB')

    cost_uuid = uuid4()
    building_cost_factory(building_cost_id=cost_uuid, building=building, material_amount=1, material_ticker='MCG')

    buildingcost = GameBuildingCost.objects.get(building_cost_id=cost_uuid)

    assert str(buildingcost) == 'HBB (Foo) (1xMCG)'
