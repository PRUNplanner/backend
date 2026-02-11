from datetime import timedelta

from core.env import settings
from django.db import models
from django.utils import timezone

EXPIRY_TIME = timedelta(minutes=settings.email.verification_expiry_minutes)


class VerificationeCodeChoices(models.TextChoices):
    EMAIL_VERIFICATION = 'Email Verification'
    PASSWORD_RESET = 'Password Reset'


class VerificationCode(models.Model):
    user = models.ForeignKey('user.User', on_delete=models.CASCADE, related_name='verification_codes')
    code = models.CharField(max_length=8)
    purpose = models.CharField(max_length=20, choices=VerificationeCodeChoices)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    objects: models.Manager['VerificationCode'] = models.Manager()

    class Meta:
        db_table = 'prunplanner_verification_codes'
        indexes = [
            models.Index(fields=['user', 'code', 'purpose']),
        ]
        verbose_name = 'Verification Code'
        verbose_name_plural = 'Verification Codes'

    def __str__(self):
        return f'{self.user.email} - {self.purpose} - {self.code}'

    @property
    def is_expired(self):
        return timezone.now() > self.created_at + EXPIRY_TIME
