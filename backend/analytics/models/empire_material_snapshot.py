from django.db import models
from planning.models import PlanningEmpire


class AnalyticsEmpireMaterialSnapshot(models.Model):
    id = models.BigAutoField(primary_key=True)

    empire = models.ForeignKey(PlanningEmpire, on_delete=models.CASCADE, related_name='material_snapshot')

    material_ticker = models.CharField(max_length=3, db_index=True)

    production = models.DecimalField(max_digits=20, decimal_places=6, default=0.0)
    consumption = models.DecimalField(max_digits=20, decimal_places=6, default=0.0)
    delta = models.DecimalField(max_digits=20, decimal_places=6, default=0.0)

    class Meta:
        db_table = 'prunplanner_statistics_empire_material_snapshot'
        verbose_name = 'Empire Material Snapshot'
        verbose_name_plural = 'Empire Material Snapshots'

        indexes = [
            models.Index(fields=['material_ticker', 'delta'], name='idx_material_ticker_delta'),
            models.Index(fields=['empire', 'delta'], name='idx_empire_delta'),
        ]

        constraints = [
            models.UniqueConstraint(fields=['empire', 'material_ticker'], name='unique_empire_material_ticker')
        ]

    def __str__(self) -> str:
        return f'{self.empire.uuid} | {self.material_ticker}: {self.delta}'
