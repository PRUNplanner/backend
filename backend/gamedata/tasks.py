from collections import Counter
from collections.abc import Callable
from datetime import timedelta
from typing import Literal

import httpx
import structlog
from celery import chord, shared_task
from core.services.cache_manager import CacheManager
from django.db import connection, transaction
from django.db.models import F, Max, Q
from django.utils import timezone

from gamedata.fio.schemas import FIOWebhookRootSchema
from gamedata.fio.services import get_fio_service
from gamedata.gamedata_cache_manager import CXPC, EXCHANGES, GamedataCacheManager
from gamedata.services.cxpc_refresh import CXPCFetch, cxpc_window_start_ms, select_cxpc_pairs

logger = structlog.get_logger(__name__)


@shared_task(name='gamedata_refresh_exchanges')
def refresh_exchanges() -> bool:
    from gamedata.fio.importers import import_all_exchanges

    return import_all_exchanges()


@shared_task(name='gamedata_refresh_planet_infrastructure')
def gamedata_refresh_planet_infrastructure(planet_natural_id: str) -> bool:
    from gamedata.fio.importers import import_planet_infrastructure

    try:
        import_planet_infrastructure(planet_natural_id)
        return True
    except Exception:
        logger.exception('planet_infrastructure_refresh_failed', planet_natural_id=planet_natural_id)
        return False


@shared_task(name='gamedata_refresh_planet')
def gamedata_refresh_planet() -> bool:
    from gamedata.fio.importers import import_planet
    from gamedata.models import GamePlanet

    now = timezone.now()

    to_update = (
        GamePlanet.objects.filter(
            Q(automation_next_retry_at__lte=now) | Q(automation_next_retry_at__isnull=True),
            # pending rows come back once their lease expires, so a dead worker can't strand them
            ~Q(automation_refresh_status='failed'),
            automation_error_count__lt=GamePlanet.MAX_RETRIES,
        )
        .order_by('automation_last_refreshed_at')
        .first()
    )

    if not to_update:
        return False

    # trigger infrastructure refresh
    gamedata_refresh_planet_infrastructure.delay(to_update.planet_natural_id)

    to_update.automation_refresh_status = 'pending'
    to_update.automation_next_retry_at = now + GamePlanet.PENDING_LEASE
    to_update.save(update_fields=['automation_refresh_status', 'automation_next_retry_at'])

    try:
        # records its own refresh result, success or error
        import_planet(to_update.planet_natural_id)

        return True

    except Exception as exc:
        if isinstance(exc, httpx.HTTPStatusError):
            # FIO answered with an error status: expected, no traceback
            logger.warning(
                'planet_refresh_failed',
                planet_natural_id=to_update.planet_natural_id,
                status_code=exc.response.status_code,
            )
        else:
            logger.exception('planet_refresh_failed', planet_natural_id=to_update.planet_natural_id)
        to_update.update_refresh_result(error=exc)

        return False


@shared_task(name='gamedata_refresh_single_planet')
def gamedata_refresh_single_planet(planet_natural_id: str) -> bool:
    from gamedata.fio.importers import import_planet

    # records its own refresh result, success or error
    return import_planet(planet_natural_id)


type AdminImportKind = Literal['materials', 'buildings', 'recipes', 'planets', 'exchanges']


@shared_task(name='gamedata_admin_import')
def gamedata_admin_import(kind: AdminImportKind) -> str:
    from gamedata.fio import importers

    runners: dict[str, Callable[[], object]] = {
        'materials': importers.import_all_materials,
        'buildings': importers.import_all_buildings,
        'recipes': importers.import_all_recipes,
        'planets': importers.import_all_planets,
        'exchanges': importers.import_all_exchanges,
    }

    result = runners[kind]()
    logger.info('admin_import_done', kind=kind, result=result)
    return f'{kind}: {result}'


@shared_task(name='gamedata_dispatch_fio_updates')
def gamedata_dispatch_fio_updates():
    """
    Identifies users eligible for an FIO data refresh based on activity
    and staleness, then dispatches worker tasks with appropriate priorities.
    """

    from gamedata.models import GameFIOPlayerData

    now = timezone.now()

    # Staleness Windows
    active_cut = now - timedelta(minutes=30)
    inactive_cut = now - timedelta(hours=6)
    recent_login_threshold = now - timedelta(days=1)

    # User Base
    eligible_base = (
        GameFIOPlayerData.objects
        # FIO credentials check
        .filter(
            user__prun_username__isnull=False,
            user__fio_apikey__isnull=False,
        )
        .exclude(
            user__prun_username='',
            user__fio_apikey='',
        )
        .filter(Q(automation_next_retry_at__isnull=True) | Q(automation_next_retry_at__lte=now))
    )

    # safety filters
    candidates = eligible_base
    # a pending row is held by the retry-at lease check in eligible_base, not excluded outright
    candidates = candidates.filter(automation_error_count__lt=GameFIOPlayerData.MAX_RETRIES)

    # timing filters
    candidates = candidates.filter(
        Q(automation_last_refreshed_at__isnull=True)
        | Q(user__last_login__gte=recent_login_threshold, automation_last_refreshed_at__lte=active_cut)
        | Q(automation_last_refreshed_at__lte=inactive_cut)
    )

    candidates = candidates.order_by(F('automation_last_refreshed_at').asc(nulls_first=True))[:100]

    # Dispatch
    dispatched_count = 0
    for user_id in candidates.values_list('user_id', flat=True):
        # Trigger task
        gamedata_refresh_user_fiodata.apply_async(args=[user_id])
        dispatched_count += 1

    return f'Dispatched {dispatched_count} FIO refresh tasks.'


@shared_task(name='gamedata_clean_user_fiodata')
def gamedata_clean_user_fiodata(user_id: int) -> None:
    from gamedata.models import GameFIOPlayerData

    GameFIOPlayerData.objects.filter(user_id=user_id).delete()


@shared_task(name='gamedata_refresh_user_fiodata')
def gamedata_refresh_user_fiodata(user_id: int, *_legacy_args: str) -> bool:
    # credentials are loaded here, so the FIO api key never passes through the broker.
    # _legacy_args: (prun_username, fio_apikey) of tasks queued before this change, ignored; remove next release
    log = logger.bind(user_id=user_id)

    # SUBSEQUENT REFRESH LOCK
    if not GamedataCacheManager.set_fio_refresh_lock(user_id):
        log.info('fio_refresh_skipped', skip='lock')
        return False

    # STORAGE REFRESH LOGIC

    from user.models import User

    from gamedata.models import GameFIOPlayerData

    try:
        user = User.objects.only('prun_username', 'fio_apikey').filter(id=user_id).first()
        if user is None:
            # user does not exist anymore, clean up lock key and return
            GamedataCacheManager.delete_fio_refresh_lock(user_id)
            log.info('fio_refresh_skipped', skip='user_missing')
            return False

        to_update, _ = GameFIOPlayerData.objects.get_or_create(user_id=user_id)

        try:
            with get_fio_service() as fio:
                storage_data = fio.get_user_storage(user.prun_username, user.fio_apikey)
                sites_data = fio.get_user_sites(user.prun_username, user.fio_apikey)
                warehouse_data = fio.get_user_sites_warehouses(user.prun_username, user.fio_apikey)
                ship_data = fio.get_user_ships(user.prun_username, user.fio_apikey)

            payloads = {
                'storage_data': [d.model_dump(mode='json') for d in storage_data],
                'site_data': [d.model_dump(mode='json') for d in sites_data],
                'warehouse_data': [d.model_dump(mode='json') for d in warehouse_data],
                'ship_data': [d.model_dump(mode='json') for d in ship_data],
            }
            changed = any(getattr(to_update, field) != payload for field, payload in payloads.items())

            if changed:
                for field, payload in payloads.items():
                    setattr(to_update, field, payload)
                to_update.update_refresh_result(commit=False)  # prevent commit, due to save call
                to_update.save(update_fields=[*payloads, *GameFIOPlayerData.AUTOMATION_FIELDS])
            else:
                # same data as stored: bookkeeping only, the storage cache stays valid
                to_update.update_refresh_result()

            log.info('fio_refresh_completed', changed=changed)
            return True

        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError):
                # expected for a wrong or revoked FIO key; fio_request_completed has the response
                log.warning('fio_refresh_failed', status_code=exc.response.status_code)
            else:
                log.exception('fio_refresh_failed')
            to_update.update_refresh_result(error=exc)
            # remove lock key, so retry is possible
            GamedataCacheManager.delete_fio_refresh_lock(user_id)

            return False

    except User.DoesNotExist:
        # remove lock key, so retry is possible
        GamedataCacheManager.delete_fio_refresh_lock(user_id)
        log.error('User not found')

        return False


@shared_task(name='gamedata_trigger_refresh_cxpc')
def gamedata_trigger_refresh_cxpc(full: bool = False):
    from gamedata.models import GameExchangeCXPC

    with get_fio_service() as fio:
        exchanges = fio.get_full_exchanges()

    if full:
        plan = [CXPCFetch(p.ticker, p.exchange_code, 'backfill') for p in exchanges]
    else:
        # newest stored candle per pair
        cursors = {
            (c['ticker'], c['exchange_code']): c['last']
            for c in GameExchangeCXPC.objects.values('ticker', 'exchange_code').annotate(last=Max('date_epoch'))
        }
        plan = select_cxpc_pairs(exchanges, cursors, cxpc_window_start_ms())

    header = [
        gamedata_refresh_cxpc.s(p.ticker, p.exchange_code, full=full, since_ms=p.since_ms) for p in plan if p.fetch
    ]
    logger.info(
        'cxpc_refresh_summary', full=full, pairs=len(plan), queued=len(header), **Counter(p.branch for p in plan)
    )

    # execute all tasks, then run the materialized view refresh
    callback = refresh_exchange_analytics.si()

    # a chord needs at least one header task
    if header:
        chord(header)(callback)
    else:
        callback.delay()


@shared_task(name='gamedata_refresh_cxpc')
def gamedata_refresh_cxpc(ticker: str, exchange_code: str, full: bool = False, since_ms: int | None = None):
    from gamedata.fio.importers import cxpc_objects
    from gamedata.models import GameExchangeCXPC

    log = logger.bind(ticker=ticker, exchange_code=exchange_code)

    try:
        with get_fio_service() as fio:
            cxpc_data = fio.get_cxpc(ticker, exchange_code, since_ms)

        objs = cxpc_objects(ticker, exchange_code, cxpc_data)

        if not objs:
            log.info('no_data_to_process')
            return True

        update_fields = ['open_p', 'close_p', 'high_p', 'low_p', 'volume', 'traded']
        unique_fields = ['ticker', 'exchange_code', 'date_epoch']

        with transaction.atomic():
            # full refresh, or only the days since since_ms (never later than the window start): upsert everything
            if full or since_ms is not None:
                GameExchangeCXPC.objects.bulk_create(
                    objs,
                    update_conflicts=True,
                    unique_fields=unique_fields,
                    update_fields=update_fields,
                    batch_size=1000,
                )
                log.info('objects_processed_full_update', objs=len(objs), since_ms=since_ms)

            # full history of a regular run, only upsert the window
            else:
                three_days_ago_ms = cxpc_window_start_ms()

                recent_objs = [o for o in objs if o.date_epoch >= three_days_ago_ms]
                historical_objs = [o for o in objs if o.date_epoch < three_days_ago_ms]

                # Process Historical: only for a pair without rows yet, full refreshes backfill gaps
                if (
                    historical_objs
                    and not GameExchangeCXPC.objects.filter(ticker=ticker, exchange_code=exchange_code).exists()
                ):
                    GameExchangeCXPC.objects.bulk_create(
                        historical_objs,
                        ignore_conflicts=True,
                        batch_size=1000,
                    )

                # Process Recent: UPSERT
                if recent_objs:
                    GameExchangeCXPC.objects.bulk_create(
                        recent_objs,
                        update_conflicts=True,
                        unique_fields=unique_fields,
                        update_fields=update_fields,
                        batch_size=1000,
                    )
                log.info(
                    'objects_processed_optimized',
                    total=len(objs),
                    recent=len(recent_objs),
                    historical=len(historical_objs),
                )

    except Exception:
        log.exception('cxpc_refresh_failed')
        return False

    return True


@shared_task(name='gamedata_refresh_exchange_analytics')
def refresh_exchange_analytics():
    # update materialized view
    with connection.cursor() as cursor:
        cursor.execute('REFRESH MATERIALIZED VIEW CONCURRENTLY prunplanner_game_exchanges_analytics;')

    CacheManager.invalidate(EXCHANGES)
    CacheManager.invalidate(CXPC)

    return True


@shared_task(name='gamedata_process_fio_webhook')
def gamedata_process_fio_webhook(payload):
    from gamedata.services.fio_webhook_dispatcher import FIOWebhookDispatcher

    try:
        # validate data
        validated_data = FIOWebhookRootSchema.model_validate(payload)

        # hand-off to webhook dispatcher
        FIOWebhookDispatcher.dispatch(validated_data)

    except Exception:
        logger.exception('fio_webhook_processing_failed')
