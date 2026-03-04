from unittest.mock import patch

import pytest
from gamedata.fio.importers import import_planet
from gamedata.models import GamePlanet

pytestmark = pytest.mark.django_db


class TestImportPlanet:
    def test_import_planet_success(self, httpx_mock, montem_raw_bytes):

        planet_id = 'OT-580b'

        httpx_mock.add_response(
            method='GET',
            url=f'https://rest.fnar.net/planet/{planet_id}',
            content=montem_raw_bytes,
            status_code=200,
        )

        with (
            patch('gamedata.fio.importers.planet_sync_resources') as mock_sync_res,
            patch('gamedata.fio.importers.planet_sync_cogc_programs') as mock_sync_cogc,
            patch('gamedata.fio.importers.planet_sync_production_fees') as mock_sync_fees,
            patch('gamedata.models.GameMaterial.material_id_ticker_map', return_value={}),
        ):
            result = import_planet(planet_id)

        assert result is True
        assert GamePlanet.objects.filter(planet_natural_id=planet_id).exists()

        mock_sync_res.assert_called_once()
        mock_sync_cogc.assert_called_once()
        mock_sync_fees.assert_called_once()

    def test_import_planet_failure_on_exception(self, httpx_mock, montem_raw_bytes):

        planet_natural_id = 'OT-580b'

        httpx_mock.add_response(
            method='GET',
            url=f'https://rest.fnar.net/planet/{planet_natural_id}',
            content=montem_raw_bytes,
        )

        with patch('gamedata.fio.importers.planet_sync_resources', side_effect=Exception('DB Crash')):
            result = import_planet(planet_natural_id)

        assert result is False

        planet = GamePlanet.objects.get(planet_natural_id=planet_natural_id)
        assert planet.automation_refresh_status == 'retrying'
