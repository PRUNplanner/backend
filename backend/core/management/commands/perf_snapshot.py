"""
Download the public game data from FIO once, for seed_perf to load instead of fake game data.

Materials, buildings, recipes, all planets (full) and the exchanges, fetched one
after another through the FIO client and validated with its schemas, saved as
one gzipped JSON file (FIO's own field names). See perf/README.md.
"""

import gzip
import time
from datetime import UTC, datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError, CommandParser
from gamedata.fio.schemas import (
    FIOBuildingSchema,
    FIOExchangeCXPC,
    FIOExchangeSchema,
    FIOMaterialSchema,
    FIOPlanetSchema,
    FIORecipeSchema,
)
from gamedata.fio.services import get_fio_service
from pydantic import BaseModel

# backend repo root / perf / snapshot, gitignored
SNAPSHOT_PATH = Path(__file__).resolve().parents[4] / 'perf' / 'snapshot' / 'gamedata.json.gz'
CXPC_EXCHANGE = 'AI1'


class GamedataSnapshot(BaseModel):
    downloaded_at: datetime
    materials: list[FIOMaterialSchema]
    buildings: list[FIOBuildingSchema]
    recipes: list[FIORecipeSchema]
    planets: list[FIOPlanetSchema]
    exchanges: list[FIOExchangeSchema]
    # real price history for a sample of tickers on one exchange, by ticker
    cxpc_exchange: str = CXPC_EXCHANGE
    cxpc: dict[str, list[FIOExchangeCXPC]] = {}

    def to_bytes(self) -> bytes:
        # by alias, so it reads back through the same FIO schemas; mtime=0 keeps the file byte-stable
        return gzip.compress(self.model_dump_json(by_alias=True).encode(), mtime=0)

    @classmethod
    def load(cls, path: Path) -> 'GamedataSnapshot':
        return cls.model_validate_json(gzip.decompress(path.read_bytes()))


def cxpc_sample_tickers(exchanges: list[FIOExchangeSchema], count: int) -> list[str]:
    """The most demanded tickers on CXPC_EXCHANGE."""
    traded = [e for e in exchanges if e.exchange_code == CXPC_EXCHANGE and e.price_average > 0]
    traded.sort(key=lambda e: (-(e.demand or 0), e.ticker))
    return [e.ticker for e in traded[:count]]


class Command(BaseCommand):
    help = 'Download the public FIO game data into the perf snapshot file.'

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument('--out', type=Path, default=SNAPSHOT_PATH)
        parser.add_argument(
            '--cxpc-sample',
            type=int,
            default=0,
            help=f'Also download real price history for this many tickers on {CXPC_EXCHANGE}; the rest stays fake.',
        )

    def handle(self, *args: object, **options: object) -> None:
        out = options['out']
        sample = options['cxpc_sample']
        if not isinstance(out, Path) or not isinstance(sample, int) or sample < 0:
            raise CommandError('--out must be a path and --cxpc-sample >= 0')

        started = time.perf_counter()
        # one request at a time, with the client's per-endpoint timeouts
        with get_fio_service() as fio:
            snapshot = GamedataSnapshot(
                downloaded_at=datetime.now(tz=UTC),
                materials=fio.get_all_materials(),
                buildings=fio.get_all_buildings(),
                recipes=fio.get_all_recipes(),
                planets=fio.get_all_planets(),
                exchanges=fio.get_all_exchanges(),
            )
            for ticker in cxpc_sample_tickers(snapshot.exchanges, sample):
                snapshot.cxpc[ticker] = fio.get_cxpc(ticker, CXPC_EXCHANGE)

        out.parent.mkdir(parents=True, exist_ok=True)
        partial = out.with_name(out.name + '.partial')  # seed_perf --source auto must never see half a file
        partial.write_bytes(snapshot.to_bytes())
        partial.replace(out)

        self.stdout.write(
            f'  materials {len(snapshot.materials):,}, buildings {len(snapshot.buildings):,}, '
            f'recipes {len(snapshot.recipes):,}, planets {len(snapshot.planets):,}, '
            f'exchanges {len(snapshot.exchanges):,}, cxpc tickers {len(snapshot.cxpc):,}'
        )
        self.stdout.write(
            self.style.SUCCESS(
                f'Wrote {out} ({out.stat().st_size / 1e6:.1f} MB) in {time.perf_counter() - started:.1f}s'
            )
        )
