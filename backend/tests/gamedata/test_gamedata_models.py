from uuid import uuid4

import pytest
from django.db import models
from gamedata.models import GameBuilding, GameBuildingCost, GameExchangeCXPC

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


@pytest.mark.xfail(
    strict=True,
    reason='audit: idx_ticker_exchange duplicates the leading columns of unique_ticker_exchange_date',
)
def test_cxpc_has_no_index_covered_by_its_unique_constraint():
    unique_fields = [
        tuple(c.fields) for c in GameExchangeCXPC._meta.constraints if isinstance(c, models.UniqueConstraint)
    ]

    for index in GameExchangeCXPC._meta.indexes:
        index_fields = tuple(index.fields)
        assert not any(fields[: len(index_fields)] == index_fields for fields in unique_fields), index.name
