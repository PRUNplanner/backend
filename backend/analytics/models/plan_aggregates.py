from django.db import models


class AnalyticsPlanAggregate(models.Model):
    planet_natural_id = models.CharField(max_length=7, unique=True, db_index=True)
    total_plans_analyzed = models.PositiveIntegerField(default=0)

    insights_data = models.JSONField(default=dict)

    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'prunplanner_statistics_plan_aggregate'
        verbose_name = 'Plan Aggregate'
        verbose_name_plural = 'Plan Aggregates'
        ordering = ['-last_updated']

    def __str__(self) -> str:
        return f'Insights for {self.planet_natural_id} (n={self.total_plans_analyzed})'
