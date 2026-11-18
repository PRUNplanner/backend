from django.conf import settings
from django.db import models
from rest_framework_api_key.models import AbstractAPIKey, APIKeyManager


class UserAPIKeyManager(APIKeyManager):
    def get_usable_keys(self) -> models.QuerySet[AbstractAPIKey]:
        # authentication reads key.user right after the lookup
        return super().get_usable_keys().select_related('user')


class UserAPIKey(AbstractAPIKey):
    class Scope(models.TextChoices):
        READ = 'read', 'Read-only'
        READ_WRITE = 'read_write', 'Read & write'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='api_keys')
    last_used = models.DateTimeField(null=True, blank=True)
    # read: safe methods only, enforced in UserAPIKeyAuthentication; keys from before 0019 are read_write
    scope = models.CharField(max_length=10, choices=Scope.choices, default=Scope.READ)

    objects: APIKeyManager['UserAPIKey'] = UserAPIKeyManager()

    class Meta(AbstractAPIKey.Meta):
        db_table = 'prunplanner_user_api_keys'
        verbose_name = 'API Key'
        verbose_name_plural = 'API Keys'
