from pathlib import Path

import pytest


@pytest.fixture
def montem_raw_bytes():
    path = Path('backend/tests/fixtures/fxt_fio_montem.json')
    return path.read_bytes()


def fixture_bytes(name: str) -> bytes:
    return Path(f'backend/tests/fixtures/{name}.json').read_bytes()
