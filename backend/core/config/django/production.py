from core.env import settings

from .base import *  # noqa: F403

DEBUG: bool = False

ALLOWED_HOSTS = settings.django_allowed_hosts.split(',')

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
