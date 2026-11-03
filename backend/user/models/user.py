from datetime import datetime

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.db.models.functions import Upper
from django.utils import timezone

from user.manager.user_manager import CustomUserManager

# users with both FIO credentials set (the queryset twin of User._has_fio_credentials, minus the strip)
FIO_LINKED_Q = (
    models.Q(prun_username__isnull=False, fio_apikey__isnull=False)
    & ~models.Q(prun_username='')
    & ~models.Q(fio_apikey='')
)


def token_cutoff() -> datetime:
    # whole seconds like a JWT's iat, so a login in the same second as the cutoff still counts
    return timezone.now().replace(microsecond=0)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.BigAutoField(primary_key=True)
    username = models.CharField(max_length=150, unique=True)
    email = models.EmailField(blank=True, null=True, unique=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    is_email_verified = models.BooleanField(default=False)
    # empty for users from before the field existed (added without a default, so no backfill)
    date_joined = models.DateTimeField(null=True, blank=True, default=timezone.now, db_index=True)

    prun_username = models.CharField(max_length=255, null=True, blank=True, default=None)  # noqa: DJ001
    fio_apikey = models.CharField(max_length=255, null=True, blank=True, default=None)  # noqa: DJ001

    # JWTs issued before this are rejected (password change, reset, deactivation); empty: all tokens count
    tokens_valid_after = models.DateTimeField(null=True, blank=True)

    objects = CustomUserManager()

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = []

    def cut_off_tokens(self) -> None:
        """Ends every session issued before now; the caller saves."""
        self.tokens_valid_after = token_cutoff()

    def issued_before_cutoff(self, iat: int) -> bool:
        return self.tokens_valid_after is not None and iat < self.tokens_valid_after.timestamp()

    def _has_fio_credentials(self) -> bool:
        # .strip() to remove " " and "\n"
        prun = (self.prun_username or '').strip()
        apikey = (self.fio_apikey or '').strip()

        return bool(prun) and bool(apikey)

    def __str__(self) -> str:
        return f'{self.username} ({self.id})'

    class Meta:
        db_table = 'prunplanner_users'
        # email__iexact / username__iexact compile to UPPER(col::text) = UPPER(%s) on Postgres
        indexes = [
            models.Index(Upper('email'), name='user_email_upper_idx'),
            models.Index(Upper('username'), name='user_username_upper_idx'),
        ]
        verbose_name = 'User'
        verbose_name_plural = 'Users'
