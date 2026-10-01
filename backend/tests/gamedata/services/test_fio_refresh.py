from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from gamedata.gamedata_cache_manager import GamedataCacheManager
from gamedata.models import GameFIOPlayerData
from gamedata.services.fio_refresh import FioRefreshReason, fio_connection, request_fio_refresh
from model_bakery import baker
from user.models import User

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('locmem_cache')]

CaptureOnCommit = Callable[..., AbstractContextManager[object]]


def _minutes_ago(minutes: int) -> datetime:
    return timezone.now() - timedelta(minutes=minutes)


def _request(
    user_id: int, reason: FioRefreshReason, capture_on_commit: CaptureOnCommit, caplog: pytest.LogCaptureFixture
) -> tuple[bool, dict[str, object]]:
    """Returns whether the refresh task was queued and the one fio_refresh_* line logged for it."""
    caplog.clear()
    with patch('gamedata.tasks.gamedata_refresh_user_fiodata.delay') as refresh, capture_on_commit(execute=True):
        queued = request_fio_refresh(user_id, reason)

    assert refresh.call_count == (1 if queued else 0)
    [line] = [r.msg for r in caplog.records if isinstance(r.msg, dict) and r.msg['event'].startswith('fio_refresh_')]
    assert line['event'] == ('fio_refresh_queued' if queued else 'fio_refresh_skipped')
    assert line['reason'] == reason
    return queued, line


@pytest.fixture
def user() -> User:
    # created with credentials, which queues nothing here: on-commit callbacks of the test transaction never run
    return baker.make('user.User', prun_username='Name', fio_apikey='key')


RowFactory = Callable[..., GameFIOPlayerData]


@pytest.mark.parametrize(
    ('reason', 'refreshed_minutes_ago', 'skip'),
    [
        ('token_refresh', 5, 'interval'),
        ('token_refresh', 16, None),
        # login and new credentials refresh right away
        ('login', 5, None),
        ('credentials', 5, None),
    ],
)
def test_only_token_refresh_waits_for_the_interval(
    user: User,
    fio_playerdata_factory: RowFactory,
    reason: FioRefreshReason,
    refreshed_minutes_ago: int,
    skip: str | None,
    django_capture_on_commit_callbacks: CaptureOnCommit,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fio_playerdata_factory(user=user, automation_last_refreshed_at=_minutes_ago(refreshed_minutes_ago))

    queued, line = _request(user.id, reason, django_capture_on_commit_callbacks, caplog)

    assert queued is (skip is None)
    assert line.get('skip') == skip


@pytest.mark.parametrize('reason', ['login', 'token_refresh'])
def test_user_never_refreshed_is_queued(
    user: User,
    reason: FioRefreshReason,
    django_capture_on_commit_callbacks: CaptureOnCommit,
    caplog: pytest.LogCaptureFixture,
) -> None:
    queued, _ = _request(user.id, reason, django_capture_on_commit_callbacks, caplog)

    assert queued is True


@pytest.mark.parametrize('reason', ['login', 'token_refresh'])
@pytest.mark.parametrize(
    ('row_fields', 'skip'),
    [
        ({'automation_error_count': GameFIOPlayerData.MAX_RETRIES, 'automation_refresh_status': 'failed'}, 'failed'),
        (
            {
                'automation_error_count': 3,
                'automation_refresh_status': 'retrying',
                'automation_next_retry_at': timezone.now() + timedelta(days=1),
            },
            'backoff',
        ),
    ],
    ids=['failed', 'backoff'],
)
def test_failed_and_backoff_rows_queue_nothing(
    user: User,
    fio_playerdata_factory: RowFactory,
    reason: FioRefreshReason,
    row_fields: dict[str, object],
    skip: str,
    django_capture_on_commit_callbacks: CaptureOnCommit,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fio_playerdata_factory(user=user, automation_last_refreshed_at=_minutes_ago(60), **row_fields)

    queued, line = _request(user.id, reason, django_capture_on_commit_callbacks, caplog)

    assert queued is False
    assert line['skip'] == skip


def test_backoff_that_has_passed_is_queued(
    user: User,
    fio_playerdata_factory: RowFactory,
    django_capture_on_commit_callbacks: CaptureOnCommit,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fio_playerdata_factory(
        user=user,
        automation_error_count=3,
        automation_refresh_status='retrying',
        automation_next_retry_at=_minutes_ago(1),
        automation_last_refreshed_at=_minutes_ago(60),
    )

    queued, _ = _request(user.id, 'token_refresh', django_capture_on_commit_callbacks, caplog)

    assert queued is True


@pytest.mark.parametrize('reason', ['login', 'credentials', 'token_refresh'])
def test_lock_holds_every_reason(
    user: User,
    reason: FioRefreshReason,
    django_capture_on_commit_callbacks: CaptureOnCommit,
    caplog: pytest.LogCaptureFixture,
) -> None:
    GamedataCacheManager.set_fio_refresh_lock(user.id)

    queued, line = _request(user.id, reason, django_capture_on_commit_callbacks, caplog)

    assert queued is False
    assert line['skip'] == 'lock'


@pytest.mark.parametrize(('prun_username', 'fio_apikey'), [(None, None), ('Name', None), ('Name', ' ')])
def test_no_credentials_queues_nothing(
    prun_username: str | None,
    fio_apikey: str | None,
    django_capture_on_commit_callbacks: CaptureOnCommit,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user: User = baker.make('user.User', prun_username=prun_username, fio_apikey=fio_apikey)

    queued, line = _request(user.id, 'login', django_capture_on_commit_callbacks, caplog)

    assert queued is False
    assert line['skip'] == 'no_credentials'


def test_missing_user_queues_nothing(
    django_capture_on_commit_callbacks: CaptureOnCommit, caplog: pytest.LogCaptureFixture
) -> None:
    queued, line = _request(999_999, 'login', django_capture_on_commit_callbacks, caplog)

    assert queued is False
    assert line['skip'] == 'no_credentials'


class TestFioConnection:
    def test_no_credentials_is_none(self) -> None:
        assert fio_connection(baker.make('user.User')) == ('none', None)

    def test_no_row_yet_is_syncing(self) -> None:
        assert fio_connection(baker.make('user.User', prun_username='Name', fio_apikey='key')) == ('syncing', None)

    @pytest.mark.parametrize(
        'code, errors, status, has_time',
        [
            (None, 0, 'syncing', False),
            (200, 0, 'ok', True),
            (204, 0, 'no_data', True),
            (401, GameFIOPlayerData.MAX_RETRIES, 'invalid_credentials', False),
            (200, 1, 'error', True),
            (None, 3, 'error', False),
        ],
    )
    def test_status_from_the_row(self, code: int | None, errors: int, status: str, has_time: bool) -> None:
        user: User = baker.make('user.User', prun_username='Name', fio_apikey='key')
        row: GameFIOPlayerData = baker.make(
            'gamedata.GameFIOPlayerData', user=user, fio_status_code=code, automation_error_count=errors
        )

        assert fio_connection(user) == (status, row.automation_last_refreshed_at if has_time else None)
