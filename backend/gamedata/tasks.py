from datetime import timedelta

import structlog
from celery import chord, shared_task
from django.db import connection
from django.db.models import F, Q
from django.utils import timezone

from gamedata.fio.services import get_fio_service
from gamedata.gamedata_cache_manager import GamedataCacheManager

logger = structlog.get_logger(__name__)


@shared_task(name='gamedata_refresh_exchanges')
def refresh_exchanges() -> bool:
    structlog.contextvars.bind_contextvars(
        task_category='gamedata_refresh_exchanges',
    )
    from gamedata.fio.importers import import_all_exchanges

    status = import_all_exchanges()

    if status:
        return status
    else:
        return status


@shared_task(name='gamedata_refresh_planet_infrastructure')
def gamedata_refresh_planet_infrastructure(planet_natural_id: str) -> bool:
    structlog.contextvars.bind_contextvars(
        task_category='gamedata_refresh_planet_infrastructure',
    )

    from gamedata.fio.importers import import_planet_infrastructure

    try:
        import_planet_infrastructure(planet_natural_id)
        return True
    except Exception:
        return False


@shared_task(name='gamedata_refresh_planet')
def gamedata_refresh_planet() -> bool:
    structlog.contextvars.bind_contextvars(
        task_category='gamedata_refresh_planet',
    )

    from gamedata.fio.importers import import_planet
    from gamedata.models import GamePlanet

    now = timezone.now()

    to_update = (
        GamePlanet.objects.filter(
            Q(automation_next_retry_at__lte=now) | Q(automation_next_retry_at__isnull=True),
            ~Q(automation_refresh_status__in=['pending', 'failed']),
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
    to_update.save()

    try:
        import_planet(to_update.planet_natural_id)
        to_update.update_refresh_result()

        return True

    except Exception as exc:
        to_update.update_refresh_result(error=exc)

        return False


@shared_task(name='gamedata_dispatch_fio_updates')
def gamedata_dispatch_fio_updates():
    structlog.contextvars.bind_contextvars(
        task_category='gamedata_dispatch_fio_updates',
    )

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
        GameFIOPlayerData.objects.select_related('user')
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
    candidates = candidates.filter(automation_error_count__lt=GameFIOPlayerData.MAX_RETRIES).exclude(
        automation_refresh_status='pending'
    )

    # timing filters
    candidates = candidates.filter(
        Q(automation_last_refreshed_at__isnull=True)
        | Q(user__last_login__gte=recent_login_threshold, automation_last_refreshed_at__lte=active_cut)
        | Q(automation_last_refreshed_at__lte=inactive_cut)
    )

    candidates = candidates.order_by(F('automation_last_refreshed_at').asc(nulls_first=True))[:100]

    # Dispatch
    dispatched_count = 0
    for storage in candidates:
        user = storage.user

        # Trigger task
        gamedata_refresh_user_fiodata.apply_async(args=[user.id, user.prun_username, user.fio_apikey])
        dispatched_count += 1

    return f'Dispatched {dispatched_count} FIO refresh tasks.'


@shared_task(name='gamedata_clean_user_fiodata')
def gamedata_clean_user_fiodata(user_id: int) -> None:
    structlog.contextvars.bind_contextvars(
        task_category='gamedata_clean_user_fiodata',
    )

    from gamedata.models import GameFIOPlayerData

    GameFIOPlayerData.objects.filter(user_id=user_id).delete()


@shared_task(name='gamedata_refresh_user_fiodata')
def gamedata_refresh_user_fiodata(user_id: int, prun_username: str, fio_apikey: str) -> bool:
    structlog.contextvars.bind_contextvars(
        task_category='gamedata_refresh_user_fiodata',
    )

    from user.models import User

    from gamedata.models import GameFIOPlayerData

    try:
        _user = User.objects.get(id=user_id)

        to_update, _created = GameFIOPlayerData.objects.get_or_create(user_id=user_id)

        if not to_update:
            return False

        try:
            with get_fio_service() as fio:
                storage_data = fio.get_user_storage(prun_username, fio_apikey)
                sites_data = fio.get_user_sites(prun_username, fio_apikey)
                warehouse_data = fio.get_user_sites_warehouses(prun_username, fio_apikey)
                ship_data = fio.get_user_ships(prun_username, fio_apikey)

            to_update.storage_data = [d.model_dump(mode='json') for d in storage_data]
            to_update.site_data = [d.model_dump(mode='json') for d in sites_data]
            to_update.warehouse_data = [d.model_dump(mode='json') for d in warehouse_data]
            to_update.ship_data = [d.model_dump(mode='json') for d in ship_data]

            to_update.update_refresh_result(commit=False)  # prevent commit, due to save call
            to_update.save(
                update_fields=[
                    'storage_data',
                    'site_data',
                    'warehouse_data',
                    'ship_data',
                    # automation fields, as commit = False
                    'automation_refresh_status',
                    'automation_error',
                    'automation_last_refreshed_at',
                    'automation_next_retry_at',
                    'automation_error_count',
                ]
            )

            return True

        except Exception as exc:
            print(exc)
            to_update.update_refresh_result(error=exc)
            return False

    except User.DoesNotExist:
        return False


@shared_task(name='gamedata_trigger_refresh_cxpc')
def gamedata_trigger_refresh_cxpc():
    structlog.contextvars.bind_contextvars(
        task_category='gamedata_trigger_refresh_cxpc',
    )

    with get_fio_service() as fio:
        exchanges_all = fio.get_all_exchanges()

    # create material ticker + exchange code pairs
    header = [gamedata_refresh_cxpc.s(p.ticker, p.exchange_code) for p in exchanges_all]

    # execute all tasks, then run the materialized view refresh
    callback = refresh_exchange_analytics.si()

    chord(header)(callback)


@shared_task(name='gamedata_refresh_cxpc')
def gamedata_refresh_cxpc(ticker, exchange_code):
    structlog.contextvars.bind_contextvars(
        task_category='gamedata_refresh_cxpc',
    )

    from gamedata.models import GameExchangeCXPC

    log = logger.bind(name='fetch_create_exchange_cxpc', ticker=ticker, exchange_code=exchange_code)

    count_before = GameExchangeCXPC.objects.filter(ticker=ticker, exchange_code=exchange_code).count()

    try:
        with get_fio_service() as fio:
            cxpc_data = fio.get_cxpc(ticker, exchange_code)

        objs = []

        for item in cxpc_data:
            if item.interval == 'DAY_ONE':
                objs.append(
                    GameExchangeCXPC(
                        ticker=ticker,
                        exchange_code=exchange_code,
                        date_epoch=item.date_epoch,
                        open_p=item.open,
                        close_p=item.close,
                        high_p=item.high,
                        low_p=item.low,
                        volume=item.volume,
                        traded=item.traded,
                    )
                )

        # bulk insert
        if objs:
            GameExchangeCXPC.objects.bulk_create(objs, ignore_conflicts=True)
            log.info('objects_bulk', objs=len(objs))

    except Exception as exc:
        log.error('exception', exc_info=exc)
        return False

    count_after = GameExchangeCXPC.objects.filter(ticker=ticker, exchange_code=exchange_code).count()

    log.info('objects_created', created=(count_before - count_after))

    return True


@shared_task(name='gamedata_refresh_exchange_analytics')
def refresh_exchange_analytics():
    structlog.contextvars.bind_contextvars(
        task_category='gamedata_refresh_exchange_analytics',
    )

    # update materialized view
    with connection.cursor() as cursor:
        cursor.execute('REFRESH MATERIALIZED VIEW CONCURRENTLY prunplanner_game_exchanges_analytics;')

    GamedataCacheManager.delete(GamedataCacheManager.key_exchange_list())
    GamedataCacheManager.delete_pattern('*cxpc*')

    return True
