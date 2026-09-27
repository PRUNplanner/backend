import importlib
from collections.abc import Callable

import pytest
from django.apps import apps
from gamedata.models import GameBuilding, GamePlanet

pytestmark = pytest.mark.django_db

migration = importlib.import_module('gamedata.migrations.0023_blank_to_null')


def test_blank_to_null(
    building_factory: Callable[..., GameBuilding], planet_factory: Callable[..., GamePlanet]
) -> None:
    building = building_factory(expertise='')
    kept = building_factory(expertise='METALLURGY')
    planet = planet_factory(faction_code='', faction_name='', cogc_program_status='')

    migration.blank_to_null(apps, None)

    building.refresh_from_db()
    kept.refresh_from_db()
    planet.refresh_from_db()
    assert building.expertise is None
    assert kept.expertise == 'METALLURGY'
    assert (planet.faction_code, planet.faction_name, planet.cogc_program_status) == (None, None, None)
