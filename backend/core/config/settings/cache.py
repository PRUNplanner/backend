# CACHING

from core.env import settings

CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': settings.cache.default_location,
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'CONNECTION_POOL_KWARGS': {'max_connections': settings.cache.max_connections, 'retry_on_timeout': True},
            'SERIALIZER': 'django_redis.serializers.pickle.PickleSerializer',
            'SOCKET_CONNECT_TIMEOUT': 5,
            'SOCKET_TIMEOUT': 5,
        },
    }
}
