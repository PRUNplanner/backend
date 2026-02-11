from django.db import models
from django.utils import timezone


class AppStatistic(models.Model):
    date = models.DateField(default=timezone.now, unique=True)

    # metrics
    ## user
    user_count = models.PositiveIntegerField(default=0)
    users_active_today = models.PositiveIntegerField(default=0)
    users_active_30d = models.PositiveIntegerField(default=0)

    ## planning
    plan_count = models.PositiveIntegerField(default=0)
    empire_count = models.PositiveIntegerField(default=0)
    cx_count = models.PositiveIntegerField(default=0)
    plan_empire_junctions_count = models.PositiveIntegerField(default=0)

    ## deltas
    user_count_delta = models.IntegerField(default=0)
    plan_count_delta = models.IntegerField(default=0)
    empire_count_delta = models.IntegerField(default=0)
    cx_count_delta = models.IntegerField(default=0)

    ## game data
    material_count = models.PositiveIntegerField(default=0)
    building_count = models.PositiveIntegerField(default=0)
    building_cost_count = models.PositiveIntegerField(default=0)
    exchange_analytics_count = models.PositiveIntegerField(default=0)
    exchange_cxpc_count = models.PositiveIntegerField(default=0)
    planet_count = models.PositiveIntegerField(default=0)
    planet_cogc_count = models.PositiveIntegerField(default=0)
    planet_productionfee_count = models.PositiveIntegerField(default=0)
    planet_resource_count = models.PositiveIntegerField(default=0)
    recipe_count = models.PositiveIntegerField(default=0)
    recipe_input_count = models.PositiveIntegerField(default=0)
    recipe_output_count = models.PositiveIntegerField(default=0)

    # meta
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'prunplanner_statistics_app'
        verbose_name = 'App Statistic'
        verbose_name_plural = 'App Statistics'

    def __str__(self) -> str:
        return f'App Statistic: {self.date}'
