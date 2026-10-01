from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from core.services.cache_manager import CacheManager
from django.forms.models import model_to_dict
from gamedata.fio.importers import (
    import_all_buildings,
    import_all_exchanges,
    import_all_materials,
    import_planet,
    import_planet_infrastructure,
    save_exchanges,
)
from gamedata.gamedata_cache_manager import BUILDINGS, EXCHANGES, MATERIALS, PLANET, PLANET_LIST
from gamedata.models import GamePlanet, GamePlanetCOGCProgram, GamePlanetProductionFee, GamePlanetResource
from model_bakery import baker

pytestmark = pytest.mark.django_db

PART_FLAGS = ('changed_planet', 'changed_resources', 'changed_cogc', 'changed_fees')


def _part_flags(caplog: pytest.LogCaptureFixture) -> list[dict[str, bool]]:
    return [
        {flag: r.msg[flag] for flag in PART_FLAGS}
        for r in caplog.records
        if isinstance(r.msg, dict) and r.msg['event'] == 'planet_refresh_completed'
    ]


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
            patch('gamedata.fio.importers.planet_sync_resources', return_value=False) as mock_sync_res,
            patch('gamedata.fio.importers.planet_sync_cogc_programs', return_value=False) as mock_sync_cogc,
            patch('gamedata.fio.importers.planet_sync_production_fees', return_value=False) as mock_sync_fees,
            patch('gamedata.models.GameMaterial.material_id_ticker_map', return_value={}),
        ):
            result = import_planet(planet_id)

        assert result is True
        assert GamePlanet.objects.filter(planet_natural_id=planet_id).exists()

        mock_sync_res.assert_called_once()
        mock_sync_cogc.assert_called_once()
        mock_sync_fees.assert_called_once()

    def test_import_planet_failure_on_exception(self, httpx_mock, montem_raw_bytes, caplog):

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
        assert [r.msg['planet_natural_id'] for r in caplog.records if r.msg['event'] == 'planet_refresh_failed'] == [
            planet_natural_id
        ]

    def test_import_all_exchanges_logs_its_failure(self, caplog):
        with patch('gamedata.fio.importers.get_fio_service', side_effect=ValueError('boom')):
            assert import_all_exchanges() is False

        assert [r.levelname for r in caplog.records if r.msg['event'] == 'exchanges_refresh_failed'] == ['ERROR']


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

    def test_import_planet_invalidates_only_on_the_first_of_two_identical_imports(self, import_montem, caplog):
        before = CacheManager.key(PLANET, 'retrieve', scope='OT-580b')

        first = import_montem()
        second = import_montem()

        assert first != before
        assert second == first
        assert [
            (r.msg['planet_natural_id'], r.msg['changed'])
            for r in caplog.records
            if r.msg['event'] == 'planet_refresh_completed'
        ] == [('OT-580b', True), ('OT-580b', False)]
        assert _part_flags(caplog)[-1] == dict.fromkeys(PART_FLAGS, False)

    @pytest.mark.parametrize(
        ('alter_stored', 'changed_part'),
        [
            (lambda: GamePlanet.objects.update(gravity=99.0), 'changed_planet'),
            (lambda: GamePlanetResource.objects.update(factor=0.987), 'changed_resources'),
            (lambda: GamePlanetResource.objects.earliest('factor').delete(), 'changed_resources'),
            (lambda: GamePlanetCOGCProgram.objects.earliest('start_epochms').delete(), 'changed_cogc'),
            (
                lambda: GamePlanetCOGCProgram.objects.create(
                    planet=GamePlanet.objects.get(), program_type=None, start_epochms=1, end_epochms=2
                ),
                'changed_cogc',
            ),
            (lambda: GamePlanetProductionFee.objects.update(fee_amount=0.123), 'changed_fees'),
        ],
        ids=['scalar', 'resource-factor', 'resource-missing', 'cogc-missing', 'cogc-stale', 'production-fee'],
    )
    def test_import_planet_invalidates_when_fio_differs_from_stored(
        self, import_montem, caplog, alter_stored: Callable[[], object], changed_part: str
    ):
        first = import_montem()
        alter_stored()  # queryset writes: no signal, so only the import can invalidate

        assert import_montem() != first
        assert _part_flags(caplog)[-1] == {flag: flag == changed_part for flag in PART_FLAGS}

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

    @pytest.mark.parametrize(
        ('stored_periods', 'fio_periods', 'created', 'deleted'),
        [
            ([], [], 0, 0),
            ([5], [5], 0, 0),
            ([5], [5, 6], 1, 0),
            ([1, 5], [5], 0, 1),
        ],
        ids=['no-reports', 'same-reports', 'new-report', 'pruned-report'],
    )
    def test_import_planet_infrastructure_invalidates_only_on_change(
        self, caplog, stored_periods: list[int], fio_periods: list[int], created: int, deleted: int
    ):
        planet = baker.make('gamedata.GamePlanet', planet_natural_id='OT-580b')
        for period in stored_periods:
            baker.make('gamedata.GamePlanetInfrastructureReport', planet=planet, simulation_period=period)
        before = CacheManager.key(PLANET, 'popr', scope='OT-580b')
        fio = _fio_returning(
            get_planet_infrastructure=SimpleNamespace(infrastructure_reports=[_fio_report(p) for p in fio_periods])
        )

        with patch('gamedata.fio.importers.get_fio_service', return_value=fio):
            assert import_planet_infrastructure('OT-580b') is True

        assert (CacheManager.key(PLANET, 'popr', scope='OT-580b') != before) is bool(created or deleted)
        assert [
            (r.msg['planet_natural_id'], r.msg['created'], r.msg['deleted'])
            for r in caplog.records
            if r.msg['event'] == 'planet_infrastructure_refresh_completed'
        ] == [('OT-580b', created, deleted)]


@pytest.fixture
def import_montem(httpx_mock, montem_raw_bytes, django_capture_on_commit_callbacks) -> Callable[[], str]:
    """Imports Montem from FIO and returns the planet detail cache key afterwards."""

    def run() -> str:
        httpx_mock.add_response(method='GET', url='https://rest.fnar.net/planet/OT-580b', content=montem_raw_bytes)
        with (
            patch('gamedata.models.GameMaterial.material_id_ticker_map', return_value={}),
            django_capture_on_commit_callbacks(execute=True),
        ):
            assert import_planet('OT-580b') is True
        return CacheManager.key(PLANET, 'retrieve', scope='OT-580b')

    return run


def _fio_report(simulation_period: int) -> SimpleNamespace:
    """A FIO infrastructure report stand-in for the given period."""
    prepared = baker.prepare('gamedata.GamePlanetInfrastructureReport', simulation_period=simulation_period)
    values = model_to_dict(prepared, exclude=['id', 'planet'])
    return SimpleNamespace(simulation_period=simulation_period, model_dump=lambda: values)


def _fio_returning(**methods: object) -> MagicMock:
    """A `get_fio_service()` stand-in whose service returns the given payloads."""
    context = MagicMock()
    for name, payload in methods.items():
        getattr(context.__enter__.return_value, name).return_value = payload
    return context
