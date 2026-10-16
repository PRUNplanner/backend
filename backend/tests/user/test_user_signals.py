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
from user.models import User, VerificationCode, VerificationeCodeChoices

pytestmark = pytest.mark.django_db


class TestUserPreSaveCost:
    def test_save_reads_previous_row_at_most_once(self) -> None:
        user: User = baker.make('user.User')

        with CaptureQueriesContext(connection) as ctx:
            user.save(update_fields=['last_login'])

        selects = [q for q in ctx.captured_queries if q['sql'].lstrip().upper().startswith('SELECT')]
        assert len(selects) <= 1

    def test_email_change_still_resets_verification(self, django_capture_on_commit_callbacks) -> None:
        user: User = baker.make('user.User', email='old@example.com', is_email_verified=True)

        with (
            patch('user.tasks.send_email_verification_code.apply_async') as mock_send,
            django_capture_on_commit_callbacks(execute=True),
        ):
            user.email = 'new@example.com'
            user.save()

        user.refresh_from_db()
        assert user.is_email_verified is False
        mock_send.assert_called_once()


def _with_email(email: str, verified: bool) -> User:
    user: User = baker.make('user.User')
    # update(): no signal, so the user starts without codes
    User.objects.filter(pk=user.pk).update(email=email, is_email_verified=verified)
    user.refresh_from_db()
    return user


class TestEmailSignals:
    def test_adding_an_email_queues_one_code_after_commit(self, django_capture_on_commit_callbacks) -> None:
        user: User = baker.make('user.User')

        with patch('user.tasks.send_email_verification_code.apply_async') as send:
            with django_capture_on_commit_callbacks() as callbacks:
                user.email = 'pilot@example.com'
                user.save()
            send.assert_not_called()
            for callback in callbacks:
                callback()

        send.assert_called_once()
        assert VerificationCode.objects.filter(user=user).count() == 1
        assert user.is_email_verified is False

    def test_changing_a_confirmed_email_drops_old_codes(self, django_capture_on_commit_callbacks) -> None:
        user = _with_email('old@example.com', verified=True)
        old = [baker.make('user.VerificationCode', user=user, purpose=purpose) for purpose in VerificationeCodeChoices]

        with (
            patch('user.tasks.send_email_verification_code.apply_async') as send,
            django_capture_on_commit_callbacks(execute=True),
        ):
            user.email = 'new@example.com'
            user.save()

        user.refresh_from_db()
        assert user.is_email_verified is False
        assert not VerificationCode.objects.filter(pk__in=[c.pk for c in old]).exists()
        (new,) = VerificationCode.objects.filter(user=user)
        assert new.purpose == VerificationeCodeChoices.EMAIL_VERIFICATION
        send.assert_called_once()

    @pytest.mark.parametrize('cleared', [None, ''])
    def test_clearing_the_email_unconfirms_and_drops_codes(
        self, django_capture_on_commit_callbacks, cleared: str | None
    ) -> None:
        user = _with_email('pilot@example.com', verified=True)
        for purpose in VerificationeCodeChoices:
            baker.make('user.VerificationCode', user=user, purpose=purpose)

        with (
            patch('user.tasks.send_email_verification_code.apply_async') as send,
            django_capture_on_commit_callbacks(execute=True),
        ):
            user.email = cleared
            user.save()

        user.refresh_from_db()
        assert user.is_email_verified is False
        assert not VerificationCode.objects.filter(user=user).exists()
        send.assert_not_called()

    def test_migration_flag_skips_everything(self, django_capture_on_commit_callbacks) -> None:
        user = _with_email('pilot@example.com', verified=True)

        with django_capture_on_commit_callbacks() as callbacks:
            user._migration_in_progress = True  # ty:ignore[unresolved-attribute]
            user.email = None
            user.save()

        user.refresh_from_db()
        assert user.is_email_verified is True
        assert callbacks == []


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
            fio_status_code=401,
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
        assert (
            row.automation_refresh_status,
            row.automation_error_count,
            row.automation_next_retry_at,
            row.fio_status_code,
        ) == ('ok', 0, None, None)

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
