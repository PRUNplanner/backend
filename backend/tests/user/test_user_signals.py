from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from gamedata.gamedata_cache_manager import GamedataCacheManager
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
    def test_save_skips_refresh_while_lock_is_held(self, django_capture_on_commit_callbacks) -> None:
        user: User = baker.make('user.User', prun_username='Name', fio_apikey='key')
        GamedataCacheManager.set_fio_refresh_lock(user.id)

        with (
            patch('gamedata.tasks.gamedata_refresh_user_fiodata.delay') as refresh,
            django_capture_on_commit_callbacks(execute=True),
        ):
            user.save(update_fields=['last_login'])

        refresh.assert_not_called()

    def test_credential_change_refreshes_despite_lock(self, django_capture_on_commit_callbacks) -> None:
        user: User = baker.make('user.User', prun_username='Name', fio_apikey='key')
        GamedataCacheManager.set_fio_refresh_lock(user.id)

        with (
            patch('gamedata.tasks.gamedata_refresh_user_fiodata.delay') as refresh,
            django_capture_on_commit_callbacks(execute=True),
        ):
            user.fio_apikey = 'new-key'
            user.save()

        refresh.assert_called_once_with(user.id)
