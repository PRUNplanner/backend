from datetime import UTC, datetime
from pathlib import Path

import orjson
import pytest
from core.management.commands.perf_snapshot import GamedataSnapshot

type FIOPayloads = dict[str, list[dict[str, object]]]

MONTEM_PATH = Path('backend/tests/fixtures/fxt_fio_montem.json')


@pytest.fixture(autouse=True)
def no_default_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep seed_perf --source auto on fake data even if a real snapshot was downloaded."""
    monkeypatch.setattr('core.management.commands.seed_perf.SNAPSHOT_PATH', tmp_path / 'no-snapshot.json.gz')


def _material(material_id: str, ticker: str) -> dict[str, object]:
    return {
        'MaterialId': material_id,
        'CategoryName': 'test',
        'CategoryId': 'cat',
        'Name': ticker.lower(),
        'Ticker': ticker,
        'Weight': 1.0,
        'Volume': 1.0,
    }


def _recipe(
    building: str, name: str, inputs: list[tuple[str, int]], outputs: list[tuple[str, int]]
) -> dict[str, object]:
    return {
        'StandardRecipeName': f'{building}:{name}',
        'RecipeName': name,
        'BuildingTicker': building,
        'TimeMs': 3_600_000,
        'Inputs': [{'Ticker': t, 'Amount': a} for t, a in inputs],
        'Outputs': [{'Ticker': t, 'Amount': a} for t, a in outputs],
    }


def _building(ticker: str, expertise: str) -> dict[str, object]:
    return {
        'BuildingId': ticker.lower().ljust(32, '0'),
        'Name': ticker.lower(),
        'Ticker': ticker,
        'Expertise': expertise,
        'Pioneers': 10,
        'Settlers': 0,
        'Technicians': 0,
        'Engineers': 0,
        'Scientists': 0,
        'AreaCost': 20,
        'BuildingCosts': [{'CommodityTicker': 'BSE', 'Amount': 4}],
    }


def _exchange(ticker: str, code: str, price: float, demand: int) -> dict[str, object]:
    return {'MaterialTicker': ticker, 'ExchangeCode': code, 'PriceAverage': price, 'Demand': demand}


@pytest.fixture
def fio_payloads() -> FIOPayloads:
    """A tiny FIO universe in FIO's wire format: Montem plus an unnamed copy, and what they need."""
    montem = orjson.loads(MONTEM_PATH.read_bytes())
    unnamed = {**montem, 'PlanetId': 'f' * 32, 'PlanetNaturalId': 'OT-580c', 'PlanetName': 'OT-580c'}
    return {
        'materials': [
            # Montem's resources
            _material('ec8dbb1d3f51d89c61b6f58fdd64a7f0', 'H2O'),
            _material('6e16dbf050b98d9c4fc9c615b3367a0f', 'NE'),
            _material('1f9a0293d9ba9bf519f71432e695edeb', 'LST'),
            _material('b9640b0d66e7d0ca7e4d3132711c97fc', 'FEO'),
            _material('a' * 32, 'DW'),
            _material('b' * 32, 'RAT'),
        ],
        'buildings': [_building('FP', 'FOOD_INDUSTRIES'), _building('BMP', 'MANUFACTURING')],
        'recipes': [
            _recipe('FP', '1xH2O=>10xDW', [('H2O', 1)], [('DW', 10)]),
            _recipe('FP', '2xH2O 1xLST=>4xRAT', [('H2O', 2), ('LST', 1)], [('RAT', 4)]),
            _recipe('BMP', '1xFEO=>1xNE', [('FEO', 1)], [('NE', 1)]),
        ],
        'planets': [montem, unnamed],
        'exchanges': [
            _exchange('DW', 'AI1', 80.0, 5000),
            _exchange('RAT', 'AI1', 120.0, 3000),
            _exchange('H2O', 'NC1', 30.0, 9000),
        ],
        'cxpc': [
            {
                'Interval': interval,
                'DateEpochMs': 1_750_000_000_000,
                'Open': 79.0,
                'Close': 81.0,
                'High': 82.0,
                'Low': 78.0,
                'Volume': 8100.0,
                'Traded': 100,
            }
            for interval in ('DAY_ONE', 'HOUR_ONE')
        ],
    }


@pytest.fixture
def snapshot_path(tmp_path: Path, fio_payloads: FIOPayloads) -> Path:
    """A snapshot file with real price history for DW on AI1, as perf_snapshot --cxpc-sample 1 writes it."""
    snapshot = GamedataSnapshot.model_validate(
        {
            'downloaded_at': datetime(2026, 9, 1, tzinfo=UTC),
            **{key: rows for key, rows in fio_payloads.items() if key != 'cxpc'},
            'cxpc': {'DW': fio_payloads['cxpc']},
        }
    )
    path = tmp_path / 'gamedata.json.gz'
    path.write_bytes(snapshot.to_bytes())
    return path
