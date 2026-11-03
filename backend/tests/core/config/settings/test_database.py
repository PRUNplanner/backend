import importlib

import pytest
from core.config.settings import database
from core.env import settings
from django.db import DatabaseError, connection, transaction


@pytest.mark.parametrize(
    'service_type, timeout_ms',
    [('django', 15_000), ('celery', 120_000), ('manage', 0)],
    ids=['web', 'worker', 'migrate'],
)
def test_statement_timeout_follows_the_service(
    monkeypatch: pytest.MonkeyPatch, service_type: str, timeout_ms: int
) -> None:
    monkeypatch.setattr(settings, 'service_type', service_type)
    try:
        reloaded = importlib.reload(database)
        assert reloaded.STATEMENT_TIMEOUT_MS == timeout_ms
        assert reloaded.DATABASES['default']['OPTIONS']['options'] == f'-c statement_timeout={timeout_ms}'
    finally:
        monkeypatch.undo()
        importlib.reload(database)


@pytest.mark.django_db
@pytest.mark.skipif(connection.vendor != 'postgresql', reason='Postgres only')
def test_web_connections_carry_the_statement_timeout() -> None:
    with connection.cursor() as cursor:
        cursor.execute('SHOW statement_timeout')
        assert cursor.fetchone() == ('15s',)


@pytest.mark.django_db
@pytest.mark.skipif(connection.vendor != 'postgresql', reason='Postgres only')
def test_a_query_past_the_timeout_is_cancelled() -> None:
    # the connection's 15 s, shortened for this transaction so the test stays fast
    with pytest.raises(DatabaseError, match='statement timeout'), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute('SET LOCAL statement_timeout = 50')
        cursor.execute('SELECT pg_sleep(1)')
