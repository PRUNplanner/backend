import importlib

import pytest
from django.apps import apps
from gamedata.models import GameFIOPlayerData
from model_bakery import baker

pytestmark = pytest.mark.django_db

migration = importlib.import_module('gamedata.migrations.0025_fio_status_code_backfill')

REJECTED = "Client error '401 Unauthorized' for url 'https://rest.fnar.net/storage/x'"
DOWN = "Server error '503 Service Unavailable' for url 'https://rest.fnar.net/storage/x'"


@pytest.mark.parametrize(
    'errors, error, code, expected',
    [
        (0, None, None, 200),
        (GameFIOPlayerData.MAX_RETRIES, REJECTED, None, 401),
        (3, DOWN, None, None),
        (0, None, 204, 204),
    ],
    ids=['ok', 'rejected-key', 'other-failure', 'already-set'],
)
def test_backfill(errors: int, error: str | None, code: int | None, expected: int | None) -> None:
    row: GameFIOPlayerData = baker.make(
        'gamedata.GameFIOPlayerData', automation_error_count=errors, automation_error=error, fio_status_code=code
    )

    migration.backfill(apps, None)

    row.refresh_from_db()
    assert row.fio_status_code == expected
