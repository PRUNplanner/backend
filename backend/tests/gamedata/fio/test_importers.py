from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from core.services.cache_manager import CacheManager
from gamedata.fio.importers import (
    import_all_buildings,
    import_all_materials,
    import_planet,
    import_planet_infrastructure,
    save_exchanges,
)
from gamedata.gamedata_cache_manager import BUILDINGS, EXCHANGES, MATERIALS, PLANET, PLANET_LIST
from gamedata.models import GamePlanet
from model_bakery import baker

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
        before = CacheManager.key(PLANET, 'retrieve', scope='OT-580b')

        with (
            patch('gamedata.models.GameMaterial.material_id_ticker_map', return_value={}),
            django_capture_on_commit_callbacks(execute=True),
        ):
            assert import_planet('OT-580b') is True

        assert CacheManager.key(PLANET, 'retrieve', scope='OT-580b') != before

    @pytest.mark.usefixtures('montem_response')
    def test_import_planet_leaves_planet_list_to_its_ttl(self, django_capture_on_commit_callbacks):
        # planets refresh every ~9s; the list expires on its short ttl instead of being rebuilt per import
        before = CacheManager.key(PLANET_LIST, 'list')

        with (
            patch('gamedata.models.GameMaterial.material_id_ticker_map', return_value={}),
            django_capture_on_commit_callbacks(execute=True),
        ):
            assert import_planet('OT-580b') is True

        assert CacheManager.key(PLANET_LIST, 'list') == before

    def test_import_all_materials_invalidates_material_list(self):
        before = CacheManager.key(MATERIALS, 'list')

        with patch('gamedata.fio.importers.get_fio_service', return_value=_fio_returning(get_all_materials=[])):
            import_all_materials()

        assert CacheManager.key(MATERIALS, 'list') != before

    def test_import_all_buildings_invalidates_building_list(self):
        before = CacheManager.key(BUILDINGS, 'list')

        with patch('gamedata.fio.importers.get_fio_service', return_value=_fio_returning(get_all_buildings=[])):
            import_all_buildings()

        assert CacheManager.key(BUILDINGS, 'list') != before

    def test_save_exchanges_invalidates_exchange_list(self):
        before = CacheManager.key(EXCHANGES, 'list')

        save_exchanges([])

        assert CacheManager.key(EXCHANGES, 'list') != before

    def test_import_planet_infrastructure_invalidates_planet_popr(self):
        baker.make('gamedata.GamePlanet', planet_natural_id='OT-580b')
        before = CacheManager.key(PLANET, 'popr', scope='OT-580b')
        fio = _fio_returning(get_planet_infrastructure=SimpleNamespace(infrastructure_reports=[]))

        with patch('gamedata.fio.importers.get_fio_service', return_value=fio):
            assert import_planet_infrastructure('OT-580b') is True

        assert CacheManager.key(PLANET, 'popr', scope='OT-580b') != before


def _fio_returning(**methods: object) -> MagicMock:
    """A `get_fio_service()` stand-in whose service returns the given payloads."""
    context = MagicMock()
    for name, payload in methods.items():
        getattr(context.__enter__.return_value, name).return_value = payload
    return context
