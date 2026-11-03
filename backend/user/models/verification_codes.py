import secrets
import string
from datetime import timedelta

from core.env import settings
from django.db import models
from django.utils import timezone
from django.utils.crypto import salted_hmac


class VerificationeCodeChoices(models.TextChoices):
    EMAIL_VERIFICATION = 'Email Verification'
    PASSWORD_RESET = 'Password Reset'


EXPIRY_TIME = {
    VerificationeCodeChoices.EMAIL_VERIFICATION: timedelta(minutes=settings.email.verification_expiry_minutes),
    VerificationeCodeChoices.PASSWORD_RESET: timedelta(minutes=settings.email.password_reset_expiry_minutes),
}

CODE_CHARS = string.ascii_uppercase + string.digits
CODE_LENGTH = 8


def new_code_secret() -> str:
    return secrets.token_hex(16)


class VerificationCode(models.Model):
    user = models.ForeignKey('user.User', on_delete=models.CASCADE, related_name='verification_codes')
    # the code is derived from this and SECRET_KEY, so neither the DB nor a task argument holds it
    secret = models.CharField(max_length=32, default=new_code_secret, editable=False)
    purpose = models.CharField(max_length=20, choices=VerificationeCodeChoices)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    objects: models.Manager['VerificationCode'] = models.Manager()

    class Meta:
        db_table = 'prunplanner_verification_codes'
        indexes = [
            models.Index(fields=['user', 'purpose']),
        ]
        verbose_name = 'Verification Code'
        verbose_name_plural = 'Verification Codes'

    def __str__(self):
        # never the code: it would show in the admin and be copied into LogEntry.object_repr
        return f'{self.user.email} - {self.purpose}'

    @property
    def code(self) -> str:
        digest = salted_hmac('verification-code', self.secret).digest()
        return ''.join(CODE_CHARS[b % len(CODE_CHARS)] for b in digest[:CODE_LENGTH])

    @property
    def is_expired(self) -> bool:
        return timezone.now() > self.created_at + EXPIRY_TIME[VerificationeCodeChoices(self.purpose)]
