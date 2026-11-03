from typing import Any

import bcrypt
import structlog
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import AbstractBaseUser
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from rest_framework.exceptions import Throttled
from rest_framework.request import HttpRequest, Request
from rest_framework.throttling import BaseThrottle

User = get_user_model()
logger = structlog.get_logger(__name__)

# failed logins per username and per client ident, in a fixed window; auth_login (10/h per IP) stays on top for JWT
LOGIN_FAILURE_LIMIT = 10
LOGIN_FAILURE_WINDOW = 15 * 60


def _failure_keys(request: HttpRequest | None, username: str) -> dict[str, str]:
    keys = {'username': f'login-failures:username:{username.lower()}'}
    if request is not None:
        keys['ident'] = f'login-failures:ident:{BaseThrottle().get_ident(request)}'
    return keys


def _count_failure(key: str) -> None:
    if not cache.add(key, 1, LOGIN_FAILURE_WINDOW):
        try:
            cache.incr(key)
        except ValueError:  # expired between add and incr
            cache.add(key, 1, LOGIN_FAILURE_WINDOW)


class LegacyAwareBackend(ModelBackend):
    """
    The only authentication backend (JWT login and admin login): one password hash per attempt, failed attempts counted.
    Supports legacy $2b$ bcrypt hashes and re-hashes them with Django's native hasher on first login.
    """

    def authenticate(
        self,
        request: HttpRequest | None = None,
        username: str | None = None,
        password: str | None = None,
        **kwargs: Any,
    ) -> AbstractBaseUser | None:
        if username is None or password is None:
            return None

        keys = _failure_keys(request, username)
        counts = cache.get_many(keys.values())
        for by, key in keys.items():
            if counts.get(key, 0) >= LOGIN_FAILURE_LIMIT:
                # once per key and window, so a loop doesn't flood Axiom
                if cache.add(f'login-throttled-logged:{key}', 1, LOGIN_FAILURE_WINDOW):
                    logger.warning('login_throttled', by=by)
                # JWT login: 429 with Retry-After; admin: authenticate() turns PermissionDenied into a failed login
                if isinstance(request, Request):
                    raise Throttled(wait=LOGIN_FAILURE_WINDOW)
                raise PermissionDenied

        user = self._check(username, password)
        if user is None:
            for key in keys.values():
                _count_failure(key)
        return user

    def _check(self, username: str, password: str) -> AbstractBaseUser | None:
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            # one hash for an unknown username too, so timing doesn't tell which usernames exist
            User().set_password(password)
            return None

        # Legacy password detection
        if user.password.startswith('$2b$'):
            # the old system hashed only the first 72 bytes, as bcrypt does; bcrypt 5 raises instead of truncating
            if not (
                self.user_can_authenticate(user) and bcrypt.checkpw(password.encode()[:72], user.password.encode())
            ):
                return None

            # Password correct, upgrade to Django native hash
            user.set_password(password)
            user.save(update_fields=['password'])
            return user

        if user.check_password(password) and self.user_can_authenticate(user):
            return user

        return None
