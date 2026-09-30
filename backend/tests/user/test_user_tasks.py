from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone
from gamedata.gamedata_cache_manager import GamedataCacheManager
from model_bakery import baker
from user.models import User
from user.tasks import user_handle_post_refresh

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('locmem_cache')]

CaptureOnCommit = Callable[..., AbstractContextManager[object]]


def _run(user: User, capture_on_commit: CaptureOnCommit) -> tuple[MagicMock, MagicMock]:
    """Runs the task and its on-commit callbacks, as outside a test transaction."""
    with (
        patch('gamedata.tasks.gamedata_refresh_user_fiodata.delay') as refresh,
        patch('gamedata.tasks.gamedata_clean_user_fiodata.delay') as clean,
        capture_on_commit(execute=True),
    ):
        user_handle_post_refresh(user.id)
    return refresh, clean


class TestUserHandlePostRefresh:
    def test_queues_fio_refresh_once_for_user_with_credentials(
        self, django_capture_on_commit_callbacks: CaptureOnCommit
    ) -> None:
        user: User = baker.make('user.User', prun_username='Name', fio_apikey='key')

        refresh, _ = _run(user, django_capture_on_commit_callbacks)

        refresh.assert_called_once_with(user.id)

    @pytest.mark.parametrize(('refreshed_minutes_ago', 'queued'), [(5, False), (16, True)])
    def test_fio_refresh_waits_for_the_interval(
        self, django_capture_on_commit_callbacks: CaptureOnCommit, refreshed_minutes_ago: int, queued: bool
    ) -> None:
        user: User = baker.make('user.User', prun_username='Name', fio_apikey='key')
        baker.make(
            'gamedata.GameFIOPlayerData',
            user=user,
            automation_last_refreshed_at=timezone.now() - timedelta(minutes=refreshed_minutes_ago),
        )

        refresh, _ = _run(user, django_capture_on_commit_callbacks)

        assert refresh.call_count == (1 if queued else 0)

    def test_asks_for_the_refresh_as_token_refresh(self) -> None:
        user: User = baker.make('user.User', prun_username='Name', fio_apikey='key')

        with patch('user.tasks.request_fio_refresh') as request_refresh:
            user_handle_post_refresh(user.id)

        request_refresh.assert_called_once_with(user.id, 'token_refresh')

    def test_skips_fio_refresh_while_lock_is_held(self, django_capture_on_commit_callbacks: CaptureOnCommit) -> None:
        user: User = baker.make('user.User', prun_username='Name', fio_apikey='key')
        GamedataCacheManager.set_fio_refresh_lock(user.id)

        refresh, _ = _run(user, django_capture_on_commit_callbacks)

        refresh.assert_not_called()

    def test_user_without_credentials_queues_nothing(self, django_capture_on_commit_callbacks: CaptureOnCommit) -> None:
        user: User = baker.make('user.User', prun_username=None, fio_apikey=None)

        refresh, clean = _run(user, django_capture_on_commit_callbacks)

        refresh.assert_not_called()
        clean.assert_not_called()

    def test_updates_last_login(self, django_capture_on_commit_callbacks: CaptureOnCommit) -> None:
        user: User = baker.make('user.User', last_login=None)

        _run(user, django_capture_on_commit_callbacks)

        user.refresh_from_db()
        assert user.last_login is not None

    def test_missing_user_is_ignored(self) -> None:
        user_handle_post_refresh(999_999)
