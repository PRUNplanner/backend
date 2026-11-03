from core.env import settings

# a stuck query fails instead of holding a web worker or the single Celery worker; tasks get more room, and
# anything else (migrations, management commands under another SERVICE_TYPE) none (0)
STATEMENT_TIMEOUT_MS = {'django': 15_000, 'celery': 120_000}.get(settings.service_type, 0)

DATABASE_ROUTERS = ['legacy_migration.db_router.LegacyRouter']

# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': settings.database.name,
        'USER': settings.database.user,
        'PASSWORD': settings.database.password,
        'HOST': settings.database.host,
        'PORT': 5432,
        'CONN_MAX_AGE': 0,
        'CONN_HEALTH_CHECKS': True,
        'OPTIONS': {
            'options': f'-c statement_timeout={STATEMENT_TIMEOUT_MS}',
            'pool': {
                'min_size': 3,
                'max_size': 10,
                'timeout': 10,
                'max_waiting': 50,
            },
        },
    }
}

if settings.database.legacy_name:
    DATABASES['legacy'] = {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': settings.database.legacy_name,
        'USER': settings.database.legacy_user,
        'PASSWORD': settings.database.legacy_password,
        'HOST': settings.database.legacy_host,
        'PORT': settings.database.legacy_port,
    }  # ty:ignore[invalid-assignment]
