from core.env import settings

# Core
CELERY_BROKER_URL = settings.celery.broker_url
CELERY_RESULT_BACKEND = settings.celery.result_backend
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_ACKS_LATE = True

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
CELERY_WORKER_CONCURRENCY = 4
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = 1000
CELERY_WORKER_MAX_MEMORY_PER_CHILD = 200000

# Redis
CELERY_REDIS_MAX_CONNECTIONS = 10
CELERY_BROKER_POOL_LIMIT = 10
CELERY_RESULT_BACKEND_THREAD_SAFE = True
CELERY_TASK_IGNORE_RESULT = True
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

CELERY_TASK_ANNOTATIONS = {
    # user
    'user_send_email_verification_code': {
        'priority': 10,
    },
    'user_send_password_reset_code': {'priority': 10},
    # game data
    'gamedata_refresh_planet': {
        'priority': 4,
        'rate_limit': '1/s',
    },
    'gamedata_refresh_planet_infrastructure': {
        'rate_limit': '1/s',
    },
    'gamedata_dispatch_fio_updates': {'priority': 4},
    'gamedata_refresh_user_fiodata': {
        'priority': 4,
        'rate_limit': '3/s',
    },
    'gamedata_refresh_cxpc': {'priority': 9, 'rate_limit': '5/s', 'acks_late': True, 'ignore_results': False},
    'gamedata_refresh_exchange_analytics': {'priority': 9},
}
