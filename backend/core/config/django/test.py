from .base import *  # noqa: F403

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'  # ty:ignore[invalid-assignment]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'testing.db',  # noqa: F405
    }
}  # ty:ignore[invalid-assignment]

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.dummy.DummyCache',
    }
}

WHITENOISE_KEEP_ONLY_HASHED_FILES = True
WHITENOISE_AUTOREFRESH = True
