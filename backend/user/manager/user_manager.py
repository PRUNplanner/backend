import time
from typing import Any, cast

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.db import IntegrityError, transaction


class CustomUserManager(BaseUserManager):
    def create_user(
        self,
        username: str,
        password: str | None = None,
        email: str | None = None,
        **extra_fields: Any,
    ) -> AbstractBaseUser:
        if not username:
            raise ValueError('The Username must be set')
        email = self.normalize_email(email) if email else None
        user = cast(AbstractBaseUser, self.model(username=username, email=email, **extra_fields))
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(
        self,
        username: str,
        password: str,
        email: str | None = None,
        **extra_fields: Any,
    ) -> Any:
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(username, password, email, **extra_fields)

    def create_legacy_user(
        self,
        id: int,
        username: str,
        hashed_password: str,
        **extra_fields: Any,
    ) -> AbstractBaseUser:
        # look for existing user, no SAVE trigger yet
        user = self.model.objects.filter(id=id).first()

        if not user:
            user = self.model(id=id)

        # migration flag
        user._migration_in_progress = True

        # basic fields
        user.username = username
        user.password = hashed_password

        # set all extra fields
        for k, v in extra_fields.items():
            setattr(user, k, v)

        try:
            with transaction.atomic():
                user.save()
        except IntegrityError as exc:
            # email adress is used multiple times, reset
            if 'email' in str(exc).lower():
                user.email = f'migrated_{int(time.time())}_{user.email}'
                user._migration_in_progress = True
                user.save()
            else:
                print(f'Failed {user.username}, {user.email}')
                print(exc)

        return user
