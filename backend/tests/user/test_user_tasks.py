from unittest.mock import MagicMock, patch

import pytest
from gamedata.gamedata_cache_manager import GamedataCacheManager
from model_bakery import baker
from user.models import User
from user.tasks import user_handle_post_refresh

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('locmem_cache')]


def _run(user: User) -> tuple[MagicMock, MagicMock]:
    with (
        patch('gamedata.tasks.gamedata_refresh_user_fiodata.delay') as refresh,
        patch('gamedata.tasks.gamedata_clean_user_fiodata.delay') as clean,
    ):
        user_handle_post_refresh(user.id)
    return refresh, clean


class TestUserHandlePostRefresh:
    def test_queues_fio_refresh_for_user_with_credentials(self) -> None:
        user: User = baker.make('user.User', prun_username='Name', fio_apikey='key')

        refresh, _ = _run(user)

        refresh.assert_called_once_with(user.id)

    def test_skips_fio_refresh_while_lock_is_held(self) -> None:
        user: User = baker.make('user.User', prun_username='Name', fio_apikey='key')
        GamedataCacheManager.set_fio_refresh_lock(user.id)

        refresh, _ = _run(user)

        refresh.assert_not_called()

    def test_user_without_credentials_queues_nothing(self) -> None:
        user: User = baker.make('user.User', prun_username=None, fio_apikey=None)

        refresh, clean = _run(user)

        refresh.assert_not_called()
        clean.assert_not_called()

    def test_updates_last_login(self) -> None:
        user: User = baker.make('user.User', last_login=None)

        _run(user)

        user.refresh_from_db()
        assert user.last_login is not None
