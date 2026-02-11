from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models

from user.manager.user_manager import CustomUserManager


class User(AbstractBaseUser, PermissionsMixin):
    id = models.BigAutoField(primary_key=True)
    username = models.CharField(max_length=150, unique=True)
    email = models.EmailField(blank=True, null=True, unique=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    is_email_verified = models.BooleanField(default=False)

    prun_username = models.CharField(max_length=255, null=True, blank=True, default=None)  # noqa: DJ001
    fio_apikey = models.CharField(max_length=255, null=True, blank=True, default=None)  # noqa: DJ001

    objects = CustomUserManager()

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = []

    def _has_fio_credentials(self) -> bool:
        return bool(self.prun_username) and bool(self.fio_apikey)

    def __str__(self) -> str:
        return f'{self.username} ({self.id})'

    class Meta:
        db_table = 'prunplanner_users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'
