from django.conf import settings
from django.db import models
from rest_framework_api_key.models import AbstractAPIKey, APIKeyManager


class UserAPIKey(AbstractAPIKey):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='api_keys')
    last_used = models.DateTimeField(null=True, blank=True)

    objects: APIKeyManager['UserAPIKey'] = APIKeyManager()

    class Meta(AbstractAPIKey.Meta):
        db_table = 'prunplanner_user_api_keys'
        verbose_name = 'API Key'
        verbose_name_plural = 'API Keys'
