"""
Settings for local benchmarks and load tests (perf/run.sh).

Production-like (DEBUG off, Redis cache, pooled Postgres, bcrypt), pointed at
the throwaway stack from perf/docker-compose.perf.yml through perf/env.perf.
PERF_MODE unlocks the seed_perf and perf_bench commands.
"""

from core.env import settings

from .base import *  # noqa: F403

PERF_MODE = True

DEBUG = False  # ty:ignore[invalid-assignment]

# testserver: perf_bench drives the app through django.test.Client
ALLOWED_HOSTS = [*settings.django_allowed_hosts.split(','), 'testserver']

# New dicts rather than item assignment: the star import shares these objects with the other settings modules.
# The shared settings pin port 5432, the perf stack runs on its own port, and there is no legacy database.
_default_db = {**DATABASES['default'], 'PORT': settings.database.port}  # noqa: F405
DATABASES = {'default': _default_db}

# the load test logs every simulated user in from one IP; the other scopes stay as in production
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    'DEFAULT_THROTTLE_RATES': {
        'webhook_inbound': '3/sec',
        'auth_login': '100000/hour',
        'auth_register': '5/min',
        'auth_verify_email': '5/min',
        'auth_password_reset': '5/min',
        'profile_update': '10/min',
    },
}

EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'  # ty:ignore[invalid-assignment]
