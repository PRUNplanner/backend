from collections.abc import Callable
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from gamedata.gamedata_cache_manager import GamedataCacheManager
from gamedata.models import GameFIOPlayerData
from model_bakery import baker
from user.models import User

pytestmark = pytest.mark.django_db


class TestUserPreSaveCost:
    def test_save_reads_previous_row_at_most_once(self) -> None:
        user: User = baker.make('user.User')

        with CaptureQueriesContext(connection) as ctx:
            user.save(update_fields=['last_login'])

        selects = [q for q in ctx.captured_queries if q['sql'].lstrip().upper().startswith('SELECT')]
        assert len(selects) <= 1

    def test_email_change_still_resets_verification(self) -> None:
        user: User = baker.make('user.User', email='old@example.com', is_email_verified=True)

        with patch('user.tasks.send_email_verification_code.apply_async') as mock_send:
            user.email = 'new@example.com'
            user.save()

        user.refresh_from_db()
        assert user.is_email_verified is False
        mock_send.assert_called_once()


@pytest.mark.usefixtures('locmem_cache')
class TestTriggerFioRefresh:
    @pytest.mark.parametrize(
        'change',
        [
            lambda u: u.save(update_fields=['last_login']),
            lambda u: u.save(),
            lambda u: (setattr(u, 'email', 'new@example.com'), u.save()),
            lambda u: (setattr(u, 'is_active', False), u.save()),
        ],
        ids=['last-login', 'plain-save', 'email', 'other-field'],
    )
    def test_save_without_credential_change_queues_nothing(
        self, django_capture_on_commit_callbacks, change: Callable[[User], object]
    ) -> None:
        user: User = baker.make('user.User', prun_username='Name', fio_apikey='key')

        with (
            patch('gamedata.tasks.gamedata_refresh_user_fiodata.delay') as refresh,
            patch('user.tasks.send_email_verification_code.apply_async'),
            django_capture_on_commit_callbacks(execute=True),
        ):
            change(user)

        refresh.assert_not_called()

    def test_preference_save_queues_nothing(self, django_capture_on_commit_callbacks) -> None:
        user: User = baker.make('user.User', prun_username='Name', fio_apikey='key')

        with (
            patch('gamedata.tasks.gamedata_refresh_user_fiodata.delay') as refresh,
            django_capture_on_commit_callbacks(execute=True),
        ):
            baker.make('user.UserPreference', user=user)

        refresh.assert_not_called()

    def test_credential_change_resets_a_failed_row_and_refreshes_despite_lock(
        self, django_capture_on_commit_callbacks
    ) -> None:
        user: User = baker.make('user.User', prun_username='Name', fio_apikey='key')
        row: GameFIOPlayerData = baker.make(
            'gamedata.GameFIOPlayerData',
            user=user,
            automation_refresh_status='failed',
            automation_error_count=GameFIOPlayerData.MAX_RETRIES,
            automation_next_retry_at=timezone.now() + timedelta(minutes=10),
        )
        GamedataCacheManager.set_fio_refresh_lock(user.id)

        with (
            patch('gamedata.tasks.gamedata_refresh_user_fiodata.delay') as refresh,
            django_capture_on_commit_callbacks(execute=True),
        ):
            user.fio_apikey = 'new-key'
            user.save()

        refresh.assert_called_once_with(user.id)
        row.refresh_from_db()
        assert (row.automation_refresh_status, row.automation_error_count, row.automation_next_retry_at) == (
            'ok',
            0,
            None,
        )

    def test_new_user_with_credentials_is_refreshed(self, django_capture_on_commit_callbacks) -> None:
        with (
            patch('gamedata.tasks.gamedata_refresh_user_fiodata.delay') as refresh,
            django_capture_on_commit_callbacks(execute=True),
        ):
            user: User = baker.make('user.User', prun_username='Name', fio_apikey='key')

        refresh.assert_called_once_with(user.id)

    def test_removed_credentials_queue_the_cleanup(self, django_capture_on_commit_callbacks) -> None:
        user: User = baker.make('user.User', prun_username='Name', fio_apikey='key')

        with (
            patch('gamedata.tasks.gamedata_refresh_user_fiodata.delay') as refresh,
            patch('gamedata.tasks.gamedata_clean_user_fiodata.delay') as clean,
            django_capture_on_commit_callbacks(execute=True),
        ):
            user.fio_apikey = None
            user.save()

        refresh.assert_not_called()
        clean.assert_called_once_with(user.id)
