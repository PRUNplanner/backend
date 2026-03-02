from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone
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

        assert gamedata_refresh_user_fiodata(user.id, 'U', 'K') is (True if scenario == 'success' else False)

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
