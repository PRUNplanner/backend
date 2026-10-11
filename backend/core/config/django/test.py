import os

from .base import *  # noqa: F403

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'  # ty:ignore[invalid-assignment]

# CI sets TEST_DB=postgres to run against the production engine (DATABASE_* from the env);
# locally the suite stays on SQLite
if os.environ.get('TEST_DB') != 'postgres':
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
