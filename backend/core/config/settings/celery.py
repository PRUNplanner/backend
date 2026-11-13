from core.env import settings

# Core
CELERY_BROKER_URL = settings.celery.broker_url
CELERY_RESULT_BACKEND = settings.celery.result_backend
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_ACKS_LATE = True

# no task holds the worker indefinitely; admin imports get their own, longer limits below
CELERY_TASK_SOFT_TIME_LIMIT = 120
CELERY_TASK_TIME_LIMIT = 150
ADMIN_IMPORT_SOFT_TIME_LIMIT = 2 * 60 * 60

# JSON
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'

# Timezone + UTC
CELERY_ENABLE_UTC = True
CELERY_TIMEZONE = 'UTC'

# Results
CELERY_RESULT_EXPIRES = 3600

# Worker
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = 1000
CELERY_WORKER_MAX_MEMORY_PER_CHILD = 200000

# Redis
CELERY_REDIS_MAX_CONNECTIONS = 10
CELERY_BROKER_POOL_LIMIT = 10
CELERY_RESULT_BACKEND_THREAD_SAFE = True
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_CREATE_MISSING_QUEUES = False
CELERY_REDIS_SOCKET_KEEPALIVE = True
CELERY_REDIS_SOCKET_TIMEOUT = 30

# Priority configuration
CELERY_BROKER_TRANSPORT_OPTIONS = {
    'priority_steps': list(range(11)),
    'sep': ':',
    'queue_order_strategy': 'priority',
}
CELERY_TASK_QUEUE_MAX_PRIORITY = 10
CELERY_TASK_DEFAULT_PRIORITY = 5

# a lower number is served first: kombu's Redis transport pops the per-priority lists in ascending order
CELERY_TASK_ANNOTATIONS = {
    # user
    'user_send_email_verification_code': {
        'priority': 1,
    },
    'user_send_password_reset_code': {'priority': 1},
    'user_send_email_changed_notice': {'priority': 1},
    # game data
    'gamedata_refresh_planet': {
        'priority': 5,
        'rate_limit': '2/s',
        # just under the 9 s beat interval: a run that waited 5-8 s for a busy worker still refreshes its planet
        'expires': 8,
    },
    'gamedata_refresh_planet_infrastructure': {
        'priority': 5,
        'rate_limit': '2/s',
    },
    'gamedata_refresh_planet_plots': {
        'priority': 5,
        'rate_limit': '2/s',
    },
    # ahead of user FIO refreshes (3), so a webhook never waits behind them
    'gamedata_process_fio_webhook': {'priority': 2},
    'gamedata_dispatch_fio_updates': {'priority': 3, 'expires': 300},
    'gamedata_refresh_user_fiodata': {
        'priority': 3,
        'rate_limit': '10/s',
    },
    'gamedata_refresh_cxpc': {'priority': 9, 'rate_limit': '10/s'},
    'gamedata_refresh_exchange_analytics': {'priority': 9},
    'gamedata_warm_market_summary': {'priority': 9},
    'gamedata_warm_planet_caches': {'priority': 9, 'expires': 300},
    'gamedata_admin_import': {
        'soft_time_limit': ADMIN_IMPORT_SOFT_TIME_LIMIT,
        'time_limit': ADMIN_IMPORT_SOFT_TIME_LIMIT + 300,
    },
    # periodic tasks: a run still queued when the next one is due is dropped (expires < beat interval)
    'gamedata_refresh_exchanges': {'expires': 600},
    'gamedata_trigger_refresh_cxpc': {'expires': 3600},
    'update_daily_stats': {'expires': 1800},
    'analytics_bulk_materialize_empire_snapshots': {'expires': 1800},
    'analytics_update_plan_insight_aggregates': {'expires': 3600},
    'user_purge_verification_codes': {'expires': 3600},
}
