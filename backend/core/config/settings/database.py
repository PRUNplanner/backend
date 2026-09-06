from core.env import settings

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
                'min_size': 3,
                'max_size': 10,
                'timeout': 10,
                'max_waiting': 50,
            }
        },
    }
}
