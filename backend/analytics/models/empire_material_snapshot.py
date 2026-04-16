from django.db import models
from planning.models import PlanningEmpire


class AnalyticsEmpireMaterialSnapshot(models.Model):
    id = models.BigAutoField(primary_key=True)

    empire = models.ForeignKey(PlanningEmpire, on_delete=models.CASCADE, related_name='material_snapshot')

    material_ticker = models.CharField(max_length=3, db_index=True)

    production = models.FloatField(default=0.0)
    consumption = models.FloatField(default=0.0)
    delta = models.FloatField(default=0.0)

    class Meta:
        db_table = 'prunplanner_statistics_empire_material_snapshot'
        unique_together = ('empire', 'material_ticker')
        verbose_name = 'Empire Material Snapshot'
        verbose_name_plural = 'Empire Material Snapshots'

        indexes = [models.Index(fields=['material_ticker', 'delta'])]

    def __str__(self) -> str:
        return f'{self.empire.uuid} | {self.material_ticker}: {self.delta}'
