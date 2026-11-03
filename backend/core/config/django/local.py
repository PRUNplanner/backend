from .base import *  # noqa: F403

DATA_UPLOAD_MAX_NUMBER_FIELDS = None
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'  # ty:ignore[invalid-assignment]

# the browsable API only locally: in production its forms can list other users' objects
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    'DEFAULT_RENDERER_CLASSES': [
        'api.renderers.OrjsonRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
}
