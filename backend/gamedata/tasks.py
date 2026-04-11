from datetime import timedelta

import orjson
import structlog
from celery import chord, shared_task
from django.db import connection, transaction
from django.db.models import F, Q
from django.utils import timezone
from django_redis import get_redis_connection

from gamedata.fio.schemas import FIOWebhookRootSchema
from gamedata.fio.schemas.fio_webhook import FIOWebhookExchangeEndpointSchema
from gamedata.fio.services import get_fio_service
from gamedata.gamedata_cache_manager import GamedataCacheManager
from gamedata.models.game_exchange import GameExchange

logger = structlog.get_logger(__name__)


@shared_task(name='gamedata_refresh_exchanges')
def refresh_exchanges() -> bool:
    structlog.contextvars.bind_contextvars(
        task_category='gamedata_refresh_exchanges',
    )
    from gamedata.fio.importers import import_all_exchanges

    return import_all_exchanges()


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

    log = logger.bind(name='gamedata_refresh_user_fiodata', user=user_id, prun_username=prun_username)

    # SUBSEQUENT REFRESH LOCK
    if not GamedataCacheManager.set_fio_refresh_lock(user_id):
        log.info('Skip user fio refresh, still within cooldown period')
        return False

    # STORAGE REFRESH LOGIC

    from user.models import User

    from gamedata.models import GameFIOPlayerData

    try:
        if not User.objects.filter(id=user_id).exists():
            # user does not exist anymore, clean up lock key and return
            GamedataCacheManager.delete_fio_refresh_lock(user_id)
            log.info('Skip user fio refresh, user does not exist anymore')
            return False

        to_update, _ = GameFIOPlayerData.objects.get_or_create(user_id=user_id)

        log.info('Update GameFIOPlayerData', uuid=to_update.uuid)

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
            log.error('Exception in update', exc_info=exc)
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
    structlog.contextvars.bind_contextvars(
        task_category='gamedata_trigger_refresh_cxpc',
    )

    with get_fio_service() as fio:
        exchanges_all = fio.get_all_exchanges()

    # create material ticker + exchange code pairs
    header = [gamedata_refresh_cxpc.s(p.ticker, p.exchange_code, full=full) for p in exchanges_all]

    # execute all tasks, then run the materialized view refresh
    callback = refresh_exchange_analytics.si()

    chord(header)(callback)


@shared_task(name='gamedata_refresh_cxpc')
def gamedata_refresh_cxpc(ticker: str, exchange_code: str, full: bool = False):
    structlog.contextvars.bind_contextvars(
        task_category='gamedata_refresh_cxpc',
    )

    from gamedata.models import GameExchangeCXPC

    log = logger.bind(name='fetch_create_exchange_cxpc', ticker=ticker, exchange_code=exchange_code)

    try:
        with get_fio_service() as fio:
            cxpc_data = fio.get_cxpc(ticker, exchange_code)

        objs = [
            (
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
            for item in cxpc_data
            if item.interval == 'DAY_ONE'
        ]

        if not objs:
            log.info('no_data_to_process')
            return True

        update_fields = ['open_p', 'close_p', 'high_p', 'low_p', 'volume', 'traded']
        unique_fields = ['ticker', 'exchange_code', 'date_epoch']

        with transaction.atomic():
            # full refresh, upsert everything
            if full:
                GameExchangeCXPC.objects.bulk_create(
                    objs,
                    update_conflicts=True,
                    unique_fields=unique_fields,
                    update_fields=update_fields,
                    batch_size=1000,
                )
                log.info('objects_processed_full_update', objs=len(objs))

            # optimized refresh, only upsert 3 days ago
            else:
                # Calculate threshold for 3 days ago
                now = timezone.now()
                today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
                three_days_ago = today_midnight - timedelta(days=3)
                three_days_ago_ms = int(three_days_ago.timestamp() * 1000)

                recent_objs = [o for o in objs if o.date_epoch >= three_days_ago_ms]
                historical_objs = [o for o in objs if o.date_epoch < three_days_ago_ms]

                # Process Historical: Ignore Conflicts
                if historical_objs:
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

    except Exception as exc:
        log.error('exception', exc_info=exc)
        return False

    return True


@shared_task(name='gamedata_refresh_exchange_analytics')
def refresh_exchange_analytics():
    structlog.contextvars.bind_contextvars(
        task_category='gamedata_refresh_exchange_analytics',
    )

    # update materialized view
    with connection.cursor() as cursor:
        cursor.execute('REFRESH MATERIALIZED VIEW CONCURRENTLY prunplanner_game_exchanges_analytics;')

    GamedataCacheManager.delete(GamedataCacheManager.key_exchange_list(fmt='json'))
    GamedataCacheManager.delete(GamedataCacheManager.key_exchange_list(fmt='csv'))
    GamedataCacheManager.delete_pattern('*cxpc*')

    return True


def _extract_unique_updates(validated_data: FIOWebhookRootSchema) -> dict[str, FIOWebhookExchangeEndpointSchema]:

    updates = {}
    for msg in validated_data.Data:
        if msg.Endpoint == '/cx':
            for cx_info in msg.Data:
                t_id = f'{cx_info.material_ticker}.{cx_info.exchange_code}'
                updates[t_id] = cx_info
    return updates


def _merge_and_enrich(db_obj: GameExchange, incoming: FIOWebhookExchangeEndpointSchema, fields: list[str]) -> bool:
    changed = False

    for field in fields:
        incoming_val = getattr(incoming, field, None)
        existing_val = getattr(db_obj, field, None)

        if incoming_val is not None:
            if existing_val != incoming_val:
                setattr(db_obj, field, incoming_val)
                changed = True
        elif existing_val is not None:
            setattr(incoming, field, existing_val)
    return changed


def _push_to_redis_stream(payloads: list[dict]):
    STREAM_MAX_LEN = 500

    r = get_redis_connection('default')
    with r.pipeline(transaction=False) as pipe:
        for p in payloads:
            pipe.xadd('stream:cx', {'payload': orjson.dumps(p)}, maxlen=STREAM_MAX_LEN, approximate=True)
        pipe.execute()


@shared_task(name='gamedata_process_fio_webhook')
def gamedata_process_fio_webhook(payload):
    structlog.contextvars.bind_contextvars(
        task_category='gamedata_process_fio_webhook',
    )

    log = logger.bind(name='gamedata_process_fio_webhook')

    sync_fields = ['mm_buy', 'mm_sell', 'price_average', 'ask', 'bid', 'ask_count', 'bid_count', 'supply', 'demand']
    STREAM_ALLOWED_EXCHANGES = {'AI1', 'NC1', 'IC1', 'CI1'}

    try:
        # validate data
        validated_data = FIOWebhookRootSchema.model_validate(payload)

        # deduplicate, only process one update per ticker in the payload batch
        updates_by_ticker = _extract_unique_updates(validated_data)

        # no cx updates received, return
        if not updates_by_ticker:
            return

        # bulk fetch existing GameExchange records
        ticker_ids = list(updates_by_ticker.keys())
        existing_records = {obj.ticker_id: obj for obj in GameExchange.objects.filter(ticker_id__in=ticker_ids)}

        if not existing_records:
            log.info('no_existing_tickers_found', count=len(ticker_ids))
            return

        # keep track of updates and redis pushes
        to_update_db = []
        redis_payloads = []

        # intersect: only iterate over tickers that exist in database
        for t_id, db_obj in existing_records.items():
            incoming = updates_by_ticker[t_id]

            if _merge_and_enrich(db_obj, incoming, sync_fields):
                to_update_db.append(db_obj)

            # only allow the main exchanges
            if incoming.exchange_code in STREAM_ALLOWED_EXCHANGES:
                redis_payloads.append(incoming.pubsub_dump(worker_timestamp=timezone.now().isoformat()))

        # execute databse update
        if to_update_db:
            GameExchange.objects.bulk_update(to_update_db, fields=sync_fields)

        # execute redis push
        if redis_payloads:
            _push_to_redis_stream(redis_payloads)

        log.info('sync_complete', db_updated=len(to_update_db), stream_pushed=len(redis_payloads))

    except Exception as exc:
        log.error('exception', exc_info=exc)
