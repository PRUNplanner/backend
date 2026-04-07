import uuid

from django.db import models


class WebhookSenderChoices(models.TextChoices):
    FIOAPI = 'FIO API'


class GlobalConfigWebhook(models.Model):
    path = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    sender = models.CharField(max_length=100, choices=WebhookSenderChoices)
    is_active = models.BooleanField(default=True)

    # statistics
    total_calls = models.PositiveBigIntegerField(default=0)
    last_received_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'prunplanner_config_webhooks'
        verbose_name = 'Config Webhook'
        verbose_name_plural = 'Config Webhooks'

    def __str__(self):
        return f'{self.is_active}: {self.path} <-- {self.sender}'
