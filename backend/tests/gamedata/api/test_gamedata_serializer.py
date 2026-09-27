from collections.abc import Callable

import pytest
from gamedata.api.serializer import GameBuildingSerializer, GamePlanetSerializer
from gamedata.models import GameBuilding, GamePlanet

pytestmark = pytest.mark.django_db


def test_building_blank_expertise_serializes_as_null(building_factory: Callable[..., GameBuilding]) -> None:
    building = building_factory(expertise='')

    assert GameBuildingSerializer(building).data['expertise'] is None


def test_building_expertise_defaults_to_null() -> None:
    assert GameBuilding().expertise is None


def test_planet_blank_fields_serialize_as_null(planet_factory: Callable[..., GamePlanet]) -> None:
    planet = planet_factory(faction_code='', faction_name='', cogc_program_status='')

    data = GamePlanetSerializer(planet).data

    assert data['faction_code'] is None
    assert data['faction_name'] is None
    assert data['cogc_program_status'] is None
