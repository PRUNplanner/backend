import gzip
from pathlib import Path

import httpx
import orjson
import pytest
from core.management.commands.perf_snapshot import GamedataSnapshot
from django.core.management import call_command
from pytest_httpx import HTTPXMock
from tests.core.management.commands.conftest import FIOPayloads

FIO = 'https://rest.fnar.net/'
ENDPOINTS = {
    'materials': 'material/allmaterials',
    'buildings': 'building/allbuildings',
    'recipes': 'recipes/allrecipes',
    'planets': 'planet/allplanets/full',
    'exchanges': 'exchange/all',
}


def mock_fio(httpx_mock: HTTPXMock, payloads: FIOPayloads) -> None:
    for key, path in ENDPOINTS.items():
        httpx_mock.add_response(method='GET', url=FIO + path, json=payloads[key])


class TestPerfSnapshot:
    def test_writes_the_validated_game_data(
        self, httpx_mock: HTTPXMock, fio_payloads: FIOPayloads, tmp_path: Path
    ) -> None:
        mock_fio(httpx_mock, fio_payloads)
        out = tmp_path / 'snapshot' / 'gamedata.json.gz'

        call_command('perf_snapshot', '--out', str(out))

        snapshot = GamedataSnapshot.load(out)
        assert snapshot.downloaded_at is not None
        assert [m.ticker for m in snapshot.materials] == ['H2O', 'NE', 'LST', 'FEO', 'DW', 'RAT']
        assert len(snapshot.buildings) == 2
        assert len(snapshot.recipes) == 3
        assert [p.planet_natural_id for p in snapshot.planets] == ['OT-580b', 'OT-580c']
        assert len(snapshot.planets[0].resources) == 4
        assert len(snapshot.exchanges) == 3
        assert snapshot.cxpc == {}
        # FIO's field names, so the file reads back through the FIO schemas
        assert 'PlanetNaturalId' in orjson.loads(gzip.decompress(out.read_bytes()))['planets'][0]
        assert list(out.parent.iterdir()) == [out]

    def test_requests_are_sequential_with_the_client_timeouts(
        self, httpx_mock: HTTPXMock, fio_payloads: FIOPayloads, tmp_path: Path
    ) -> None:
        mock_fio(httpx_mock, fio_payloads)

        call_command('perf_snapshot', '--out', str(tmp_path / 'gamedata.json.gz'))

        requests = httpx_mock.get_requests()
        assert [str(r.url).removeprefix(FIO) for r in requests] == list(ENDPOINTS.values())
        assert requests[3].extensions['timeout']['read'] == 10  # allplanets

    def test_cxpc_sample_downloads_the_most_demanded_tickers(
        self, httpx_mock: HTTPXMock, fio_payloads: FIOPayloads, tmp_path: Path
    ) -> None:
        mock_fio(httpx_mock, fio_payloads)
        httpx_mock.add_response(method='GET', url=FIO + 'exchange/cxpc/DW.AI1', json=fio_payloads['cxpc'])
        out = tmp_path / 'gamedata.json.gz'

        call_command('perf_snapshot', '--out', str(out), '--cxpc-sample', '1')

        snapshot = GamedataSnapshot.load(out)
        assert snapshot.cxpc_exchange == 'AI1'
        assert list(snapshot.cxpc) == ['DW']  # H2O is in higher demand, but not on AI1
        assert len(snapshot.cxpc['DW']) == 2

    def test_a_failed_download_writes_nothing(
        self, httpx_mock: HTTPXMock, fio_payloads: FIOPayloads, tmp_path: Path
    ) -> None:
        httpx_mock.add_response(method='GET', url=FIO + ENDPOINTS['materials'], json=fio_payloads['materials'])
        httpx_mock.add_response(method='GET', url=FIO + ENDPOINTS['buildings'], status_code=503)
        out = tmp_path / 'gamedata.json.gz'

        with pytest.raises(httpx.HTTPStatusError):
            call_command('perf_snapshot', '--out', str(out))

        assert list(tmp_path.iterdir()) == []
