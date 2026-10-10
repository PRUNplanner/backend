import difflib
import os
from io import StringIO
from pathlib import Path

from django.core.management import call_command

SNAPSHOT = Path(__file__).parent / 'openapi.yml'


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
