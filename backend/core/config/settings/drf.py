from datetime import timedelta

from corsheaders.defaults import default_headers

from core.env import settings

# REST FRAMEWORK

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'user.auth_apikey.LoggedJWTAuthentication',
        'user.auth_apikey.UserAPIKeyAuthentication',
        'user.auth_apikey.LoggedBasicAuthentication',
    ],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_THROTTLE_CLASSES': [
        'core.throttling.LoggedScopedRateThrottle',
        'core.throttling.AuthenticatedUserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'webhook_inbound': '3/sec',
        'auth_login': '10/hour',
        'auth_register': '5/min',
        'auth_verify_email': '5/min',
        'auth_password_reset': '5/min',
        'profile_update': '10/min',
        'client_error': '30/min',
        # every request of a logged-in user; ~4x the busiest normal minute (p99.99 65)
        'user': '240/min',
        # data/storage/ with rows/ and csv/; ~2x the busiest normal minute (p99.99 14)
        'fio_storage': '30/min',
    },
    'DEFAULT_RENDERER_CLASSES': [
        'api.renderers.OrjsonRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
    'COERCE_DECIMAL_TO_STRING': False,
}


SPECTACULAR_SETTINGS = {
    'TITLE': 'PRUNplanner API',
    'DESCRIPTION': (
        'API endpoints for PRUNplanner.org allowing Empire and Base planning and management.\n\n'
        'Scripts and sheets authenticate with an API key, created on the API Keys page: either as the header '
        '`Authorization: Api-Key <KEY>` or as the query parameter `?api_key=<KEY>` (for tools that only take a URL, '
        'such as Google Sheets `IMPORTDATA`). A key has full access to your account, so anyone who can see a URL '
        'with your key can use it. Revoke a key you no longer need.'
    ),
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SERVE_PERMISSIONS': ['rest_framework.permissions.AllowAny'],
    'SECURITY': [],
    'POSTPROCESSING_HOOKS': [],
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=settings.rest_framework.access_token_lifetime),
    'REFRESH_TOKEN_LIFETIME': timedelta(minutes=settings.rest_framework.refresh_token_lifetime),
    'UPDATE_LAST_LOGIN': True,
}

DRF_API_KEY_CUSTOM_MODEL = 'user.UserAPIKey'
CORS_ALLOW_ALL_ORIGINS = True

CORS_ALLOW_HEADERS = (
    *default_headers,
    'cache-control',
    'pragma',
    'withcredentials',
    'expires',
    # the frontend's request and session IDs, logged as request_id / correlation_id
    'x-request-id',
    'x-correlation-id',
)
CORS_PREFLIGHT_MAX_AGE = 86400
# a cross-origin browser only reads safelisted headers; the frontend waits Retry-After after a 429
CORS_EXPOSE_HEADERS = ['Retry-After']
