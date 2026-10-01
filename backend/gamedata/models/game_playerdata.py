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

    # last FIO answer on the user's storage: 200 data, 204 no data yet, 401 key rejected; None until one came
    fio_status_code = models.PositiveSmallIntegerField(
        'FIO answer', null=True, blank=True, choices=[(200, 'Data'), (204, 'No data yet'), (401, 'Key rejected')]
    )

    # what a refresh writes besides the payloads; saving only these leaves the storage cache valid
    BOOKKEEPING_FIELDS = CeleryAutomationModel.AUTOMATION_FIELDS | {'fio_status_code'}

    objects: models.Manager['GameFIOPlayerData'] = models.Manager()

    class Meta:
        db_table = 'prunplanner_game_fio_playerdata'
        verbose_name = 'FIO Player Data'
        verbose_name_plural = 'FIO Player Data'
