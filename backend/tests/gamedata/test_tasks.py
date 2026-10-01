from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from celery.canvas import Signature
from core.services.cache_manager import CacheManager
from django.utils import timezone
from gamedata.fio.importers import cxpc_objects
from gamedata.fio.schemas import FIOExchangeCXPC, FIOExchangeFullSChema
from gamedata.gamedata_cache_manager import STORAGE, GamedataCacheManager
from gamedata.models import GameExchangeCXPC
from gamedata.models.game_planet import GamePlanet
from gamedata.models.game_playerdata import GameFIOPlayerData
from gamedata.services.cxpc_refresh import cxpc_window_start_ms
from gamedata.services.fio_refresh import fio_connection
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
from pydantic import TypeAdapter

CaptureOnCommit = Callable[..., AbstractContextManager[object]]
LONG_AGO = timezone.now() - timedelta(days=1)


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

    @pytest.mark.parametrize(
        'error, level',
        [
            (httpx.HTTPStatusError('503', request=MagicMock(), response=MagicMock(status_code=503)), 'WARNING'),
            (ValueError('bug'), 'ERROR'),
        ],
    )
    def test_refresh_planet_logs_its_failure(
        self, error: Exception, level: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        baker.make('gamedata.GamePlanet', planet_natural_id='M', automation_error_count=0)

        with (
            patch('gamedata.fio.importers.import_planet', side_effect=error),
            patch('gamedata.tasks.gamedata_refresh_planet_infrastructure.delay'),
        ):
            assert gamedata_refresh_planet() is False

        failed = [
            (r.levelname, r.msg)
            for r in caplog.records
            if isinstance(r.msg, dict) and r.msg['event'] == 'planet_refresh_failed'
        ]
        assert [(lvl, msg['planet_natural_id']) for lvl, msg in failed] == [(level, 'M')]
        # a traceback only for what is not an HTTP error status
        assert ('exception' in failed[0][1]) is (level == 'ERROR')

    @pytest.mark.parametrize('scenario', ['missing', 'fio_fail', 'success'])
    @patch('gamedata.tasks.get_fio_service')
    def test_refresh_user_fiodata(self, mock_get_fio, scenario):
        user = baker.make('user.User') if scenario != 'missing' else MagicMock(id=999)
        mock_fio = mock_get_fio.return_value.__enter__.return_value
        mock_fio.get_user_storage.side_effect = Exception if scenario == 'fio_fail' else None
        mock_fio.get_user_storage.return_value = [MagicMock(model_dump=lambda **k: {})]

        assert gamedata_refresh_user_fiodata(user.id) is (True if scenario == 'success' else False)

    @pytest.mark.parametrize(
        'error, level',
        [
            (httpx.HTTPStatusError('401', request=MagicMock(), response=MagicMock(status_code=401)), 'WARNING'),
            (ValueError('bug'), 'ERROR'),
        ],
    )
    @patch('gamedata.tasks.get_fio_service')
    def test_refresh_user_fiodata_logs_a_rejected_key_as_warning(
        self, mock_get_fio, error: Exception, level: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        user = baker.make('user.User')
        mock_get_fio.return_value.__enter__.return_value.get_user_storage.side_effect = error

        assert gamedata_refresh_user_fiodata(user.id) is False
        assert [
            r.levelname for r in caplog.records if isinstance(r.msg, dict) and r.msg['event'] == 'fio_refresh_failed'
        ] == [level]

    @patch('gamedata.tasks.get_fio_service')
    def test_refresh_user_fiodata_loads_credentials_and_accepts_legacy_args(self, mock_get_fio):
        user = baker.make('user.User', prun_username='Stored', fio_apikey='stored-key')
        mock_fio = mock_get_fio.return_value.__enter__.return_value

        # a task queued before the signature change still carries (prun_username, fio_apikey)
        assert gamedata_refresh_user_fiodata(user.id, 'Old', 'old-key') is True
        mock_fio.get_user_storage.assert_called_once_with('Stored', 'stored-key')

    @patch('gamedata.tasks.get_fio_service')
    def test_refresh_user_fiodata_401_fails_at_once(self, mock_get_fio):
        user = baker.make('user.User', prun_username='Name', fio_apikey='bad')
        error = httpx.HTTPStatusError('401', request=MagicMock(), response=MagicMock(status_code=401))
        mock_get_fio.return_value.__enter__.return_value.get_user_storage.side_effect = error

        assert gamedata_refresh_user_fiodata(user.id) is False

        row = GameFIOPlayerData.objects.get(user=user)
        assert (row.automation_refresh_status, row.automation_error_count, row.fio_status_code) == (
            'failed',
            GameFIOPlayerData.MAX_RETRIES,
            401,
        )
        assert row.automation_next_retry_at is None
        assert fio_connection(user)[0] == 'invalid_credentials'

    @patch('gamedata.tasks.get_fio_service')
    def test_refresh_user_fiodata_204_is_no_data_and_keeps_the_payload(self, mock_get_fio):
        user = baker.make('user.User', prun_username='Name', fio_apikey='key')
        baker.make('gamedata.GameFIOPlayerData', user=user, storage_data=[{'old': 1}], automation_error_count=2)
        mock_fio = mock_get_fio.return_value.__enter__.return_value
        mock_fio.get_user_storage.return_value = None

        assert gamedata_refresh_user_fiodata(user.id) is True

        row = GameFIOPlayerData.objects.get(user=user)
        assert (row.storage_data, row.automation_error_count, row.fio_status_code) == ([{'old': 1}], 0, 204)
        mock_fio.get_user_sites.assert_not_called()
        assert fio_connection(user)[0] == 'no_data'

    @patch('gamedata.tasks.get_fio_service')
    def test_refresh_user_fiodata_success_records_200(self, mock_get_fio):
        user = baker.make('user.User', prun_username='Name', fio_apikey='key')
        mock_get_fio.return_value.__enter__.return_value.get_user_storage.return_value = []

        assert gamedata_refresh_user_fiodata(user.id) is True

        assert GameFIOPlayerData.objects.get(user=user).fio_status_code == 200
        assert fio_connection(user)[0] == 'ok'

    @patch('gamedata.tasks.get_fio_service')
    @patch('gamedata.tasks.chord')
    def test_cxpc_logic(self, mock_chord, mock_get_fio):
        # Trigger logic
        mock_get_fio.return_value.__enter__.return_value.get_full_exchanges.return_value = [
            SimpleNamespace(ticker='F', exchange_code='A', price_time_epochms=1)
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
        with patch('django.db.connection.cursor'), patch('gamedata.tasks.CacheManager') as m:
            assert refresh_exchange_analytics() is True
            assert m.invalidate.call_count == 2
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
class TestDispatchFioUpdatesScheduling:
    """Idle users are skipped, the rest is queued with a low priority, one every 3 s."""

    @staticmethod
    def due_user(last_login_days: int | None) -> int:
        now = timezone.now()
        user = baker.make(
            'user.User',
            prun_username='T',
            fio_apikey='K',
            last_login=None if last_login_days is None else now - timedelta(days=last_login_days),
        )
        baker.make(
            'gamedata.GameFIOPlayerData',
            user=user,
            automation_error_count=0,
            automation_refresh_status='success',
            automation_last_refreshed_at=now - timedelta(hours=7),
        )
        return user.pk

    @staticmethod
    def dispatch() -> tuple[list[int], MagicMock, MagicMock]:
        with (
            patch('gamedata.tasks.gamedata_refresh_user_fiodata.apply_async') as mock_async,
            patch('gamedata.tasks.logger') as mock_logger,
        ):
            gamedata_dispatch_fio_updates()
        return [call.kwargs['args'][0] for call in mock_async.call_args_list], mock_async, mock_logger

    def test_skips_users_without_a_login_in_7_days(self) -> None:
        day_1, day_6 = self.due_user(1), self.due_user(6)
        day_8, never = self.due_user(8), self.due_user(None)

        dispatched, _, mock_logger = self.dispatch()

        assert set(dispatched) == {day_1, day_6}
        assert day_8 not in dispatched and never not in dispatched
        mock_logger.info.assert_called_once_with('fio_dispatch_completed', dispatched=2, skipped_idle=2)

    def test_dispatches_with_low_priority_and_staggered_countdown(self) -> None:
        for _ in range(3):
            self.due_user(1)

        _, mock_async, _ = self.dispatch()

        assert [call.kwargs['priority'] for call in mock_async.call_args_list] == [7, 7, 7]
        assert [call.kwargs['countdown'] for call in mock_async.call_args_list] == [0, 3, 6]


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


DAY = 86_400_000
CXPC_ROW_FIELDS = ('ticker', 'exchange_code', 'date_epoch', 'open_p', 'close_p', 'high_p', 'low_p', 'volume', 'traded')


def _full_exchange(ticker: str, last_trade: int | None) -> FIOExchangeFullSChema:
    return FIOExchangeFullSChema.model_validate(
        {
            'MaterialTicker': ticker,
            'ExchangeCode': 'NC1',
            'PriceAverage': 1.0,
            'Traded': 0,
            'VolumeAmount': 0,
            'PriceTimeEpochMs': last_trade,
        }
    )


def _candle(date_epoch: int, interval: str = 'DAY_ONE', close: float = 2) -> FIOExchangeCXPC:
    return FIOExchangeCXPC.model_validate(
        {
            'Interval': interval,
            'DateEpochMs': date_epoch,
            'Open': 1,
            'Close': close,
            'High': 3,
            'Low': 1,
            'Volume': 10,
            'Traded': 5,
        }
    )


@pytest.mark.django_db
class TestTriggerRefreshCXPC:
    @staticmethod
    def _run(exchanges: list[FIOExchangeFullSChema], full: bool = False) -> tuple[MagicMock, MagicMock]:
        with (
            patch('gamedata.tasks.get_fio_service') as mock_get_fio,
            patch('gamedata.tasks.chord') as mock_chord,
            patch('gamedata.tasks.refresh_exchange_analytics.si') as mock_callback,
        ):
            mock_get_fio.return_value.__enter__.return_value.get_full_exchanges.return_value = exchanges
            gamedata_trigger_refresh_cxpc(full=full)
        return mock_chord, mock_callback

    @staticmethod
    def _queued(mock_chord: MagicMock) -> list[tuple[tuple[str, ...], dict[str, object]]]:
        header = mock_chord.call_args.args[0]
        return [(tuple(s.args), dict(s.kwargs)) for s in header]

    def test_queues_only_pairs_that_can_have_new_candles(self) -> None:
        window = cxpc_window_start_ms()
        baker.make('gamedata.GameExchangeCXPC', ticker='WIN', exchange_code='NC1', date_epoch=window + DAY)
        baker.make('gamedata.GameExchangeCXPC', ticker='OLD', exchange_code='NC1', date_epoch=window - 10 * DAY)

        mock_chord, mock_callback = self._run(
            [
                _full_exchange('NEV', None),  # no rows, never traded
                _full_exchange('NEW', window - 50 * DAY),  # no rows, traded
                _full_exchange('WIN', None),  # candle in window, stale last trade
                _full_exchange('OLD', window - 20 * DAY),  # nothing new
            ]
        )

        assert self._queued(mock_chord) == [
            (('NEW', 'NC1'), {'full': False, 'since_ms': None}),
            (('WIN', 'NC1'), {'full': False, 'since_ms': window}),
        ]
        mock_chord.return_value.assert_called_once_with(mock_callback.return_value)
        mock_callback.return_value.delay.assert_not_called()

    def test_nothing_to_fetch_still_refreshes_analytics(self) -> None:
        mock_chord, mock_callback = self._run([_full_exchange('NEV', None)])

        mock_chord.assert_not_called()
        mock_callback.return_value.delay.assert_called_once_with()

    def test_full_queues_every_pair_with_full_history(self) -> None:
        baker.make('gamedata.GameExchangeCXPC', ticker='OLD', exchange_code='NC1', date_epoch=1_000)

        mock_chord, _ = self._run([_full_exchange('NEV', None), _full_exchange('OLD', None)], full=True)

        assert self._queued(mock_chord) == [
            (('NEV', 'NC1'), {'full': True, 'since_ms': None}),
            (('OLD', 'NC1'), {'full': True, 'since_ms': None}),
        ]


@pytest.mark.django_db
class TestRefreshCXPCSince:
    def test_upserts_every_day_one_candle_since(self) -> None:
        window = cxpc_window_start_ms()
        baker.make(
            'gamedata.GameExchangeCXPC', ticker='RAT', exchange_code='NC1', date_epoch=window, close_p=Decimal(99)
        )

        with patch('gamedata.tasks.get_fio_service') as mock_get_fio:
            mock_fio = mock_get_fio.return_value.__enter__.return_value
            mock_fio.get_cxpc.return_value = [
                _candle(window),
                _candle(window + DAY),
                _candle(window + DAY, interval='HOUR_ONE'),
            ]
            assert gamedata_refresh_cxpc('RAT', 'NC1', since_ms=window) is True

        mock_fio.get_cxpc.assert_called_once_with('RAT', 'NC1', window)
        rows = GameExchangeCXPC.objects.filter(ticker='RAT', exchange_code='NC1').order_by('date_epoch')
        assert [(r.date_epoch, r.close_p) for r in rows] == [(window, Decimal(2)), (window + DAY, Decimal(2))]


@pytest.mark.django_db
class TestRefreshCXPCParity:
    """The regular run with since_ms stores what the full-history path stores (recorded live RAT.NC1)."""

    @staticmethod
    def _fixture() -> list[FIOExchangeCXPC]:
        raw = TypeAdapter(list[FIOExchangeCXPC]).validate_json(
            Path('backend/tests/fixtures/fxt_fio_cxpc_rat_nc1.json').read_bytes()
        )
        # move the recorded days so the newest candle is today
        today = cxpc_window_start_ms() + 3 * DAY
        shift = today - max(c.date_epoch for c in raw if c.interval == 'DAY_ONE')
        return [c.model_copy(update={'date_epoch': c.date_epoch + shift}) for c in raw]

    @staticmethod
    def _seed(candles: list[FIOExchangeCXPC], until: int | None, stale_from: int | None = None) -> None:
        GameExchangeCXPC.objects.all().delete()
        if until is None:
            return
        rows = cxpc_objects('RAT', 'NC1', [c for c in candles if c.date_epoch <= until])
        for row in rows:
            if stale_from is not None and row.date_epoch >= stale_from:
                row.close_p = Decimal(-1)
        GameExchangeCXPC.objects.bulk_create(rows)

    @staticmethod
    def _rows() -> list[tuple[object, ...]]:
        return list(GameExchangeCXPC.objects.order_by('date_epoch').values_list(*CXPC_ROW_FIELDS))

    @staticmethod
    def _old_path(candles: list[FIOExchangeCXPC], full: bool = False) -> None:
        with patch('gamedata.tasks.get_fio_service') as mock_get_fio:
            mock_get_fio.return_value.__enter__.return_value.get_cxpc.return_value = candles
            assert gamedata_refresh_cxpc('RAT', 'NC1', full=full) is True

    @staticmethod
    def _new_path(candles: list[FIOExchangeCXPC], last_trade: int) -> None:
        def get_cxpc(ticker: str, exchange_code: str, since_ms: int | None = None) -> list[FIOExchangeCXPC]:
            # what exchange/cxpc/{T.CX}/{since_ms} returns
            return [c for c in candles if since_ms is None or c.date_epoch >= since_ms]

        def run_chord(header: list[Signature]) -> MagicMock:
            # run the header tasks in-process, the analytics callback is not part of the parity
            for sig in header:
                gamedata_refresh_cxpc(*sig.args, **sig.kwargs)
            return MagicMock()

        with (
            patch('gamedata.tasks.get_fio_service') as mock_get_fio,
            patch('gamedata.tasks.chord', side_effect=run_chord) as mock_chord,
        ):
            mock_fio = mock_get_fio.return_value.__enter__.return_value
            mock_fio.get_full_exchanges.return_value = [_full_exchange('RAT', last_trade)]
            mock_fio.get_cxpc.side_effect = get_cxpc
            gamedata_trigger_refresh_cxpc()

        assert mock_chord.called

    def test_pair_with_candles_in_window_matches_old_path(self) -> None:
        candles = self._fixture()
        window = cxpc_window_start_ms()
        yesterday = window + 2 * DAY

        self._seed(candles, until=yesterday, stale_from=window)
        self._old_path(candles)
        old = self._rows()

        self._seed(candles, until=yesterday, stale_from=window)
        self._new_path(candles, last_trade=window + 3 * DAY)

        assert self._rows() == old
        assert all(row[4] != Decimal(-1) for row in old)

    def test_pair_without_rows_is_backfilled_like_old_path(self) -> None:
        candles = self._fixture()

        self._seed(candles, until=None)
        self._old_path(candles)
        old = self._rows()

        self._seed(candles, until=None)
        self._new_path(candles, last_trade=cxpc_window_start_ms())

        assert self._rows() == old
        assert len(old) == 30

    def test_late_candle_matches_full_refresh(self) -> None:
        candles = self._fixture()
        window = cxpc_window_start_ms()
        cursor = window - 4 * DAY  # 7 days ago, older than the window

        self._seed(candles, until=cursor)
        self._old_path(candles)
        old = self._rows()

        self._seed(candles, until=cursor)
        self._old_path(candles, full=True)
        full = self._rows()

        self._seed(candles, until=cursor)
        self._new_path(candles, last_trade=window - 2 * DAY)

        assert self._rows() == full
        assert len(full) > len(old)  # the regular run without since_ms never stores the late days


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

    def test_pending_mark_saves_only_the_status_and_lease(self) -> None:
        baker.make('gamedata.GamePlanet', planet_natural_id='M', automation_error_count=0)

        with patch.object(GamePlanet, 'save', autospec=True) as save:
            self._run(lambda _planet_natural_id: True)

        save.assert_called_once()
        assert save.call_args.kwargs == {'update_fields': ['automation_refresh_status', 'automation_next_retry_at']}


@pytest.mark.django_db
class TestPendingLease:
    """AC21: pending carries a 1 h lease; the scheduler takes pending rows back once the lease expired."""

    @staticmethod
    def _picked(**planet_fields: object) -> list[str]:
        baker.make(  # ty: ignore[no-matching-overload]
            'gamedata.GamePlanet', planet_natural_id='X', automation_error_count=0, **planet_fields
        )
        with (
            patch('gamedata.fio.importers.import_planet', return_value=True) as import_planet,
            patch('gamedata.tasks.gamedata_refresh_planet_infrastructure.delay'),
        ):
            gamedata_refresh_planet()
        return [call.args[0] for call in import_planet.call_args_list]

    def test_pending_without_lease_is_picked(self) -> None:
        assert self._picked(automation_refresh_status='pending', automation_next_retry_at=None) == ['X']

    def test_pending_with_expired_lease_is_picked(self) -> None:
        expired = timezone.now() - timedelta(minutes=1)
        assert self._picked(automation_refresh_status='pending', automation_next_retry_at=expired) == ['X']

    def test_pending_with_active_lease_is_not_picked(self) -> None:
        active = timezone.now() + timedelta(minutes=30)
        assert self._picked(automation_refresh_status='pending', automation_next_retry_at=active) == []

    def test_failed_stays_excluded(self) -> None:
        assert self._picked(automation_refresh_status='failed') == []

    def test_marking_pending_sets_a_one_hour_lease(self) -> None:
        planet: GamePlanet = baker.make('gamedata.GamePlanet', planet_natural_id='X', automation_error_count=0)
        seen: dict[str, object] = {}

        def import_that_looks(planet_natural_id: str) -> bool:
            row = GamePlanet.objects.get(planet_natural_id=planet_natural_id)
            seen['status'], seen['lease'] = row.automation_refresh_status, row.automation_next_retry_at
            return True

        before = timezone.now()
        with (
            patch('gamedata.fio.importers.import_planet', side_effect=import_that_looks),
            patch('gamedata.tasks.gamedata_refresh_planet_infrastructure.delay'),
        ):
            gamedata_refresh_planet()

        assert seen['status'] == 'pending'
        lease = seen['lease']
        assert isinstance(lease, type(before))
        assert before + planet.PENDING_LEASE <= lease <= timezone.now() + planet.PENDING_LEASE

    def test_fio_dispatch_takes_pending_rows_with_expired_lease_only(self) -> None:
        now = timezone.now()
        stale = now - timedelta(hours=7)

        def fio_row(lease: object) -> int:
            user = baker.make('user.User', prun_username='T', fio_apikey='K', last_login=now)
            baker.make(
                'gamedata.GameFIOPlayerData',
                user=user,
                automation_refresh_status='pending',
                automation_next_retry_at=lease,
                automation_last_refreshed_at=stale,
            )
            return user.pk

        expired, leased = fio_row(now - timedelta(minutes=1)), fio_row(now + timedelta(minutes=30))

        with patch('gamedata.tasks.gamedata_refresh_user_fiodata.apply_async') as mock_async:
            gamedata_dispatch_fio_updates()

        dispatched = [call.kwargs['args'][0] for call in mock_async.call_args_list]
        assert expired in dispatched and leased not in dispatched


@pytest.mark.django_db
class TestAdminTasks:
    @pytest.mark.parametrize(
        'kind, importer, result',
        [
            ('materials', 'import_all_materials', (1, 2)),
            ('buildings', 'import_all_buildings', (3, 4)),
            ('recipes', 'import_all_recipes', (1, 2, 3)),
            ('planets', 'import_all_planets', True),
            ('exchanges', 'import_all_exchanges', True),
        ],
    )
    def test_admin_import_dispatches_to_the_importer(self, kind: str, importer: str, result: object) -> None:
        from gamedata.tasks import gamedata_admin_import

        with patch(f'gamedata.fio.importers.{importer}', return_value=result) as run:
            assert gamedata_admin_import(kind) == f'{kind}: {result}'

        run.assert_called_once_with()

    def test_single_planet_refresh(self) -> None:
        from gamedata.tasks import gamedata_refresh_single_planet

        with patch('gamedata.fio.importers.import_planet', return_value=True) as run:
            assert gamedata_refresh_single_planet('OT-580b') is True

        run.assert_called_once_with('OT-580b')


@pytest.mark.django_db
@pytest.mark.usefixtures('locmem_cache')
class TestRefreshUserFiodataChangeDetection:
    @staticmethod
    def _refresh(
        user_id: int, amount: int, capture_on_commit: CaptureOnCommit, caplog: pytest.LogCaptureFixture
    ) -> tuple[str, bool]:
        """Runs one refresh with FIO returning `amount`; returns the storage cache key and the logged `changed`."""
        GamedataCacheManager.delete_fio_refresh_lock(user_id)
        GameFIOPlayerData.objects.filter(user_id=user_id).update(automation_last_refreshed_at=LONG_AGO)
        caplog.clear()

        with patch('gamedata.tasks.get_fio_service') as get_fio, capture_on_commit(execute=True):
            fio = get_fio.return_value.__enter__.return_value
            for call in (fio.get_user_storage, fio.get_user_sites, fio.get_user_sites_warehouses, fio.get_user_ships):
                call.return_value = [MagicMock(model_dump=lambda **_: {'Amount': amount})]
            assert gamedata_refresh_user_fiodata(user_id) is True

        [changed] = [
            r.msg['changed']
            for r in caplog.records
            if isinstance(r.msg, dict) and r.msg['event'] == 'fio_refresh_completed'
        ]
        return CacheManager.key(STORAGE, 'retrieve', scope=user_id), changed

    def test_identical_data_keeps_the_storage_cache_and_changed_data_bumps_it(
        self, django_capture_on_commit_callbacks: CaptureOnCommit, caplog: pytest.LogCaptureFixture
    ) -> None:
        user = baker.make('user.User', prun_username='Name', fio_apikey='key')

        first_key, first_changed = self._refresh(user.id, 1, django_capture_on_commit_callbacks, caplog)
        same_key, same_changed = self._refresh(user.id, 1, django_capture_on_commit_callbacks, caplog)

        assert (first_changed, same_changed) == (True, False)
        assert same_key == first_key
        # the unchanged refresh still counts as a successful one
        row = GameFIOPlayerData.objects.get(user=user)
        assert row.automation_last_refreshed_at > LONG_AGO
        assert row.storage_data == [{'Amount': 1}]

        new_key, new_changed = self._refresh(user.id, 2, django_capture_on_commit_callbacks, caplog)

        assert new_changed is True
        assert new_key != first_key
        assert GameFIOPlayerData.objects.get(user=user).storage_data == [{'Amount': 2}]
