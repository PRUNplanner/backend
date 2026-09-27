from unittest.mock import MagicMock, patch

import pytest
from gamedata.fio.importers import import_all_buildings, import_all_materials, import_planet
from gamedata.gamedata_cache_manager import GamedataCacheManager
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


@pytest.mark.usefixtures('locmem_cache')
class TestImporterCacheInvalidation:
    @pytest.fixture
    def montem_response(self, httpx_mock, montem_raw_bytes) -> None:
        httpx_mock.add_response(method='GET', url='https://rest.fnar.net/planet/OT-580b', content=montem_raw_bytes)

    @pytest.mark.usefixtures('montem_response')
    def test_import_planet_invalidates_planet_detail(self, django_capture_on_commit_callbacks):
        key = GamedataCacheManager.key_planet_get('OT-580b')
        GamedataCacheManager.set(key, b'stale', timeout=60)

        with (
            patch('gamedata.models.GameMaterial.material_id_ticker_map', return_value={}),
            django_capture_on_commit_callbacks(execute=True),
        ):
            assert import_planet('OT-580b') is True

        assert GamedataCacheManager.get(key) is None

    @pytest.mark.usefixtures('montem_response')
    def test_import_planet_invalidates_planet_list(self, django_capture_on_commit_callbacks):
        key = GamedataCacheManager.key_planet_list()
        GamedataCacheManager.set(key, b'stale', timeout=60)

        with (
            patch('gamedata.models.GameMaterial.material_id_ticker_map', return_value={}),
            django_capture_on_commit_callbacks(execute=True),
        ):
            assert import_planet('OT-580b') is True

        assert GamedataCacheManager.get(key) is None

    def test_import_all_materials_invalidates_material_list(self):
        key = GamedataCacheManager.key_material_list()
        GamedataCacheManager.set(key, b'stale', timeout=60)

        with patch('gamedata.fio.importers.get_fio_service', return_value=_fio_returning(get_all_materials=[])):
            import_all_materials()

        assert GamedataCacheManager.get(key) is None

    def test_import_all_buildings_invalidates_building_list(self):
        key = GamedataCacheManager.key_building_list()
        GamedataCacheManager.set(key, b'stale', timeout=60)

        with patch('gamedata.fio.importers.get_fio_service', return_value=_fio_returning(get_all_buildings=[])):
            import_all_buildings()

        assert GamedataCacheManager.get(key) is None


def _fio_returning(**methods: list[object]) -> MagicMock:
    """A `get_fio_service()` stand-in whose service returns the given payloads."""
    context = MagicMock()
    for name, payload in methods.items():
        getattr(context.__enter__.return_value, name).return_value = payload
    return context
