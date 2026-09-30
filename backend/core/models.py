import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone


class UUIDModel(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class ChangeTrackedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class CeleryAutomationModel(models.Model):
    automation_last_refreshed_at = models.DateTimeField(default=timezone.now)
    automation_refresh_status = models.CharField(
        max_length=20,
        choices=[('ok', 'OK'), ('pending', 'Pending'), ('retrying', 'Retrying'), ('failed', 'Failed')],
        default='ok',
        db_index=True,
    )
    automation_error = models.TextField(blank=True, null=True)  # noqa: DJ001
    automation_next_retry_at = models.DateTimeField(blank=True, null=True)
    automation_error_count = models.PositiveIntegerField(default=0)

    class Meta:
        abstract = True

        indexes = [
            # index: ready to process
            models.Index(
                fields=['automation_refresh_status', 'automation_next_retry_at', 'automation_error_count'],
                name='%(class)s_task_logic_idx',
            ),
            # index: last processed
            models.Index(fields=['automation_last_refreshed_at'], name='%(class)s_last_run_idx'),
        ]

    AUTOMATION_FIELDS = frozenset(
        {
            'automation_refresh_status',
            'automation_error',
            'automation_last_refreshed_at',
            'automation_next_retry_at',
            'automation_error_count',
        }
    )

    RETRY_DELAY_MINUTES = 15
    MAX_RETRIES = 10
    # a row marked pending holds its next_retry_at as a lease; a worker that dies leaves it to expire
    PENDING_LEASE = timedelta(hours=1)

    @classmethod
    def stuck_q(cls) -> models.Q:
        """Pending rows whose lease expired (or never had one): the worker that took them is gone."""
        return models.Q(automation_refresh_status='pending') & (
            models.Q(automation_next_retry_at__isnull=True) | models.Q(automation_next_retry_at__lte=timezone.now())
        )

    @classmethod
    def failed_q(cls) -> models.Q:
        return models.Q(automation_error_count__gte=cls.MAX_RETRIES)

    @property
    def is_permanently_failed(self) -> bool:
        return self.automation_error_count >= self.MAX_RETRIES

    def update_refresh_result(self, error: Exception | None = None, commit: bool = True) -> None:
        now = timezone.now()

        if error is None:
            self.automation_refresh_status = 'ok'
            self.automation_error = None
            self.automation_last_refreshed_at = now
            self.automation_next_retry_at = None
            self.automation_error_count = 0
        else:
            self.automation_error_count += 1
            self.automation_error = str(error)[:2000]

            if self.is_permanently_failed:
                self.automation_refresh_status = 'failed'
                self.automation_next_retry_at = None
            else:
                self.automation_refresh_status = 'retrying'
                self.automation_next_retry_at = now + timedelta(minutes=self.RETRY_DELAY_MINUTES)

        if commit:
            self.save(update_fields=self.AUTOMATION_FIELDS)
