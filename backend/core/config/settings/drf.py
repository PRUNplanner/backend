from datetime import timedelta

from corsheaders.defaults import default_headers

from core.env import settings

# REST FRAMEWORK

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'user.auth_apikey.UserAPIKeyAuthentication',
        'rest_framework.authentication.BasicAuthentication',
    ],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {'anon': '60/minute', 'user': '2000/day'},
    'DEFAULT_RENDERER_CLASSES': [
        'api.renderers.OrjsonRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
    'COERCE_DECIMAL_TO_STRING': False,
}


SPECTACULAR_SETTINGS = {
    'TITLE': 'PRUNplanner API',
    'DESCRIPTION': 'API endpoints for PRUNplanner.org allowing Empire and Base planning and management',
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

CORS_ALLOW_HEADERS = (*default_headers, 'cache-control', 'pragma', 'withcredentials', 'expires')
CORS_PREFLIGHT_MAX_AGE = 86400
