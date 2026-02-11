from .base import *  # noqa: F403

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'  # ty:ignore[invalid-assignment]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'testing.db',  # noqa: F405
    }
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.dummy.DummyCache',
    }
}
