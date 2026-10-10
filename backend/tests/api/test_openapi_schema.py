import difflib
import os
from collections.abc import Iterator
from io import StringIO
from pathlib import Path

import pytest
from django.apps import apps
from django.core.management import call_command
from django.db import connection
from django.db.backends.base.operations import BaseDatabaseOperations

SNAPSHOT = Path(__file__).parent / 'openapi.yml'


def _forget_field_validators() -> None:
    for model in apps.get_models():
        for field in model._meta.concrete_fields:
            field.__dict__.pop('validators', None)


@pytest.fixture
def postgres_integer_ranges(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # integer bounds in the schema come from the database backend (SQLite reports 64 bits for every integer
    # field); pin them to Postgres, which serves production. Fields cache their validators, so reset them around
    monkeypatch.setattr(connection.ops, 'integer_field_range', BaseDatabaseOperations.integer_field_ranges.get)
    _forget_field_validators()
    yield
    _forget_field_validators()


@pytest.mark.usefixtures('postgres_integer_ranges')
def test_openapi_schema_matches_snapshot() -> None:
    """
    The frontend validates every response against hand-written Zod schemas, so any change to a response shape can
    break it. The committed snapshot puts every API change in the PR diff. After an intended change, refresh it with
    `UPDATE_OPENAPI_SNAPSHOT=1 uv run pytest backend/tests/api/test_openapi_schema.py` and run `/sync-api`.
    """
    out = StringIO()
    call_command('spectacular', '--validate', '--fail-on-warn', stdout=out)
    schema = out.getvalue()

    if os.environ.get('UPDATE_OPENAPI_SNAPSHOT'):
        SNAPSHOT.write_text(schema)

    snapshot = SNAPSHOT.read_text()
    diff = '\n'.join(
        difflib.unified_diff(snapshot.splitlines(), schema.splitlines(), 'snapshot', 'current', lineterm='')
    )
    assert schema == snapshot, f'API schema changed; refresh the snapshot if intended (see docstring):\n{diff}'
