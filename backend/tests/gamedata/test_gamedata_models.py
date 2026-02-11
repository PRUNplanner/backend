import pytest
from gamedata.models import GameBuilding


@pytest.mark.django_db
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
