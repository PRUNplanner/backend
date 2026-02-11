from typing import Any

import bcrypt
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import AbstractBaseUser
from rest_framework.request import HttpRequest

User = get_user_model()


class LegacyAwareBackend(ModelBackend):
    """
    Authentication backend that supports legacy $2b$ bcrypt hashes.
    On first login, re-hashes the password with Django's native hasher.
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

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            return None

        # Legacy password detection
        if user.password.startswith('$2b$'):
            if bcrypt.checkpw(password.encode(), user.password.encode()):
                # Password correct, upgrade to Django native hash
                user.set_password(password)
                user.save(update_fields=['password'])
                return user
            else:
                return None

        # Otherwise, fallback to default check
        if user.check_password(password) and self.user_can_authenticate(user):
            return user

        return None
