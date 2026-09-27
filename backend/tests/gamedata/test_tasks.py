from collections.abc import Callable
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone
from gamedata.models.game_planet import GamePlanet
from gamedata.models.game_playerdata import GameFIOPlayerData
from gamedata.tasks import (
    gamedata_clean_user_fiodata,
    gamedata_dispatch_fio_updates,
    gamedata_refresh_cxpc,
    gamedata_refresh_planet,
    gamedata_refresh_planet_infrastructure,
    gamedata_refresh_user_fiodata,
    gamedata_trigger_refresh_cxpc,
    refresh_exchange_analytics,
    refresh_exchanges,
)
from model_bakery import baker


@pytest.mark.django_db
class TestGamedataTasks:
    @pytest.mark.parametrize('scenario, expected', [('ok', True), ('fail', False)])
    def test_simple_imports(self, scenario, expected):
        with (
            patch('gamedata.fio.importers.import_all_exchanges', return_value=expected),
            patch('gamedata.fio.importers.import_planet_infrastructure', side_effect=None if expected else Exception),
        ):
            assert refresh_exchanges() == expected
            assert gamedata_refresh_planet_infrastructure('M') == expected

    @pytest.mark.parametrize('scenario', ['none', 'success', 'error'])
    def test_refresh_planet(self, scenario):
        if scenario != 'none':
            baker.make('gamedata.GamePlanet', planet_natural_id='M', automation_error_count=0)

        with (
            patch('gamedata.fio.importers.import_planet', side_effect=Exception if scenario == 'error' else None),
            patch('gamedata.tasks.gamedata_refresh_planet_infrastructure.delay'),
        ):
            assert gamedata_refresh_planet() is (True if scenario == 'success' else False)

    @pytest.mark.parametrize('scenario', ['missing', 'fio_fail', 'success'])
    @patch('gamedata.tasks.get_fio_service')
    def test_refresh_user_fiodata(self, mock_get_fio, scenario):
        user = baker.make('user.User') if scenario != 'missing' else MagicMock(id=999)
        mock_fio = mock_get_fio.return_value.__enter__.return_value
        mock_fio.get_user_storage.side_effect = Exception if scenario == 'fio_fail' else None
        mock_fio.get_user_storage.return_value = [MagicMock(model_dump=lambda **k: {})]

        assert gamedata_refresh_user_fiodata(user.id) is (True if scenario == 'success' else False)

    @patch('gamedata.tasks.get_fio_service')
    def test_refresh_user_fiodata_loads_credentials_and_accepts_legacy_args(self, mock_get_fio):
        user = baker.make('user.User', prun_username='Stored', fio_apikey='stored-key')
        mock_fio = mock_get_fio.return_value.__enter__.return_value

        # a task queued before the signature change still carries (prun_username, fio_apikey)
        assert gamedata_refresh_user_fiodata(user.id, 'Old', 'old-key') is True
        mock_fio.get_user_storage.assert_called_once_with('Stored', 'stored-key')

    @patch('gamedata.tasks.get_fio_service')
    @patch('gamedata.tasks.chord')
    def test_cxpc_logic(self, mock_chord, mock_get_fio):
        # Trigger logic
        mock_get_fio.return_value.__enter__.return_value.get_all_exchanges.return_value = [
            SimpleNamespace(ticker='F', exchange_code='A')
        ]
        gamedata_trigger_refresh_cxpc()
        assert mock_chord.called

        with patch('gamedata.tasks.get_fio_service') as m:
            f = m.return_value.__enter__.return_value
            f.get_cxpc.return_value = [
                SimpleNamespace(interval='DAY_ONE', date_epoch=1, open=1, close=1, high=1, low=1, volume=1, traded=1)
            ]
            assert gamedata_refresh_cxpc('F', 'A') is True
            f.get_cxpc.side_effect = Exception
            assert gamedata_refresh_cxpc('F', 'A') is False

    def test_analytics_and_cleanup(self):
        with patch('django.db.connection.cursor'), patch('gamedata.tasks.GamedataCacheManager') as m:
            assert refresh_exchange_analytics() is True
            assert m.delete.called
        user = baker.make('user.User')
        baker.make('gamedata.GameFIOPlayerData', user=user)
        gamedata_clean_user_fiodata(user.id)
        assert not GameFIOPlayerData.objects.filter(user_id=user.id).exists()

    @patch('gamedata.tasks.gamedata_refresh_user_fiodata.apply_async')
    def test_dispatch_fio_updates(self, mock_async):
        user = baker.make('user.User', prun_username='T', fio_apikey='K', last_login=timezone.now())
        baker.make(
            'gamedata.GameFIOPlayerData',
            user=user,
            automation_error_count=0,
            automation_refresh_status='success',
            automation_last_refreshed_at=timezone.now() - timedelta(hours=7),
        )

        assert 'Dispatched 1' in gamedata_dispatch_fio_updates()
        assert mock_async.called


@pytest.mark.django_db
class TestDispatchFioUpdatesPayload:
    def test_dispatched_task_does_not_carry_the_api_key(self):
        user = baker.make('user.User', prun_username='T', fio_apikey='secret-key', last_login=timezone.now())
        baker.make(
            'gamedata.GameFIOPlayerData',
            user=user,
            automation_error_count=0,
            automation_refresh_status='ok',
            automation_last_refreshed_at=timezone.now() - timedelta(hours=7),
        )

        with patch('gamedata.tasks.gamedata_refresh_user_fiodata.apply_async') as mock_async:
            gamedata_dispatch_fio_updates()

        mock_async.assert_called_once()
        assert 'secret-key' not in repr(mock_async.call_args)


@pytest.mark.django_db
class TestRefreshCXPCHistory:
    """Non-full runs only insert history (older than 3 days) for a pair without rows yet."""

    HISTORICAL_EPOCH = 1_000

    @staticmethod
    def _point(date_epoch: int) -> SimpleNamespace:
        return SimpleNamespace(
            interval='DAY_ONE', date_epoch=date_epoch, open=1, close=2, high=3, low=1, volume=10, traded=5
        )

    def _run(self, full: bool) -> None:
        recent_epoch = int(timezone.now().timestamp() * 1000)
        with patch('gamedata.tasks.get_fio_service') as mock_get_fio:
            mock_get_fio.return_value.__enter__.return_value.get_cxpc.return_value = [
                self._point(self.HISTORICAL_EPOCH),
                self._point(recent_epoch),
            ]
            assert gamedata_refresh_cxpc('FUEL', 'AI1', full=full) is True

    def _has_history(self) -> bool:
        from gamedata.models import GameExchangeCXPC

        return GameExchangeCXPC.objects.filter(
            ticker='FUEL', exchange_code='AI1', date_epoch=self.HISTORICAL_EPOCH
        ).exists()

    def test_new_pair_gets_history(self) -> None:
        self._run(full=False)

        assert self._has_history()

    def test_known_pair_skips_history_but_upserts_recent(self) -> None:
        from gamedata.models import GameExchangeCXPC

        baker.make('gamedata.GameExchangeCXPC', ticker='FUEL', exchange_code='AI1', date_epoch=2_000)

        self._run(full=False)

        assert not self._has_history()
        assert GameExchangeCXPC.objects.filter(ticker='FUEL', exchange_code='AI1').count() == 2

    def test_full_refresh_inserts_history_for_known_pair(self) -> None:
        baker.make('gamedata.GameExchangeCXPC', ticker='FUEL', exchange_code='AI1', date_epoch=2_000)

        self._run(full=True)

        assert self._has_history()


@pytest.mark.django_db
class TestRefreshPlanetResult:
    @staticmethod
    def _run(import_side_effect: Callable[[str], bool]) -> None:
        with (
            patch('gamedata.fio.importers.import_planet', side_effect=import_side_effect),
            patch('gamedata.tasks.gamedata_refresh_planet_infrastructure.delay'),
        ):
            gamedata_refresh_planet()

    def test_failed_import_keeps_its_recorded_error(self) -> None:
        planet: GamePlanet = baker.make('gamedata.GamePlanet', planet_natural_id='M', automation_error_count=0)

        def import_that_records_an_error(planet_natural_id: str) -> bool:
            GamePlanet.objects.get(planet_natural_id=planet_natural_id).update_refresh_result(error=Exception('boom'))
            return False

        self._run(import_that_records_an_error)

        planet.refresh_from_db()
        assert planet.automation_refresh_status == 'retrying'
        assert planet.automation_error == 'boom'

    def test_pending_mark_saves_only_the_status(self) -> None:
        baker.make('gamedata.GamePlanet', planet_natural_id='M', automation_error_count=0)

        with patch.object(GamePlanet, 'save', autospec=True) as save:
            self._run(lambda _planet_natural_id: True)

        save.assert_called_once()
        assert save.call_args.kwargs == {'update_fields': ['automation_refresh_status']}
