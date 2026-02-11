from core.models import CeleryAutomationModel, UUIDModel
from django.core.validators import MinValueValidator
from django.db import models


class GameFIOPlayerData(UUIDModel, CeleryAutomationModel):
    user = models.ForeignKey('user.User', on_delete=models.CASCADE, related_name='fio_playerdata')
    schema_version = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)], db_index=True)

    storage_data = models.JSONField(default=dict)
    site_data = models.JSONField(default=dict)
    warehouse_data = models.JSONField(default=dict)
    ship_data = models.JSONField(default=dict)

    objects: models.Manager['GameFIOPlayerData'] = models.Manager()

    class Meta:
        db_table = 'prunplanner_game_fio_playerdata'
        verbose_name = 'FIO Player Data'
        verbose_name_plural = 'FIO Player Data'
