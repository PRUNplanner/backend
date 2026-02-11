from core.env import settings

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
            'pool': {
                # leanest RAM per worker
                'min_size': 1,
                'max_size': 2,
                'timeout': 10,
                'max_waiting': 10,
            }
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
    }
