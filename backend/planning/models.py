from core.models import ChangeTrackedModel, UUIDModel
from django.core.validators import MaxValueValidator, MinLengthValidator, MinValueValidator
from django.db import models


class PlanningFactionChoices(models.TextChoices):
    NONE = 'NONE'
    ANTARES = 'ANTARES'
    BENTEN = 'BENTEN'
    HORTUS = 'HORTUS'
    MORIA = 'MORIA'
    OUTSIDEREGION = 'OUTSIDEREGION'


class PlanningCOGCChoices(models.TextChoices):
    NONE = '---'
    AGRICULTURE = 'AGRICULTURE'
    CHEMISTRY = 'CHEMISTRY'
    CONSTRUCTION = 'CONSTRUCTION'
    ELECTRONICS = 'ELECTRONICS'
    FOOD_INDUSTRIES = 'FOOD_INDUSTRIES'
    FUEL_REFINING = 'FUEL_REFINING'
    MANUFACTURING = 'MANUFACTURING'
    METALLURGY = 'METALLURGY'
    RESOURCE_EXTRACTION = 'RESOURCE_EXTRACTION'
    PIONEERS = 'PIONEERS'
    SETTLERS = 'SETTLERS'
    TECHNICIANS = 'TECHNICIANS'
    ENGINEERS = 'ENGINEERS'
    SCIENTISTS = 'SCIENTISTS'


class PlanningPlan(UUIDModel, ChangeTrackedModel):
    user = models.ForeignKey('user.User', on_delete=models.CASCADE, related_name='plans')

    plan_name = models.CharField(max_length=200)

    # plan characteristics
    planet_natural_id = models.CharField(max_length=7, validators=[MinLengthValidator(7)], db_index=True)
    plan_permits_used = models.PositiveIntegerField(validators=[MaxValueValidator(3)])
    plan_cogc = models.CharField(max_length=20, choices=PlanningCOGCChoices.choices)
    plan_corphq = models.BooleanField(default=False)

    # plan data json
    plan_data = models.JSONField(default=dict)
    schema_version = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)], db_index=True)

    def __str__(self) -> str:
        return f'{self.plan_name} ({self.uuid})'

    class Meta:
        db_table = 'prunplanner_planning_plans'
        verbose_name = 'Plan'
        verbose_name_plural = 'Plans'


class PlanningEmpire(UUIDModel, ChangeTrackedModel):
    user = models.ForeignKey('user.User', on_delete=models.CASCADE, related_name='empires')

    plans = models.ManyToManyField(
        'PlanningPlan',
        through='PlanningEmpirePlan',
        related_name='empires',
        blank=True,
    )

    cx = models.ForeignKey(
        'PlanningCX',
        on_delete=models.SET_NULL,
        related_name='cxs',
        null=True,
        blank=True,
    )

    empire_name = models.CharField(max_length=200)

    # empire characteristics
    empire_faction = models.CharField(max_length=20, choices=PlanningFactionChoices.choices)
    empire_permits_used = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    empire_permits_total = models.PositiveIntegerField(validators=[MinValueValidator(2)])

    def __str__(self) -> str:
        return f'{self.empire_name} ({self.uuid})'

    class Meta:
        db_table = 'prunplanner_planning_empires'
        verbose_name = 'Empire'
        verbose_name_plural = 'Empires'


class PlanningEmpirePlan(UUIDModel):
    user = models.ForeignKey('user.User', on_delete=models.CASCADE, related_name='empire_plan_links')

    empire = models.ForeignKey(PlanningEmpire, on_delete=models.CASCADE, related_name='empire_plans')
    plan = models.ForeignKey(PlanningPlan, on_delete=models.CASCADE, related_name='plan_empires')

    def __str__(self) -> str:
        return f'{self.empire} ↔ {self.plan}'

    class Meta:
        db_table = 'prunplanner_planning_jct_empire_plan'
        unique_together = ('empire', 'plan')

        indexes = [
            # lookup: plan -> empire
            models.Index(fields=['plan', 'empire'], name='jct_plan_empire_idx'),
            # junctions by user lookup
            models.Index(fields=['user'], name='idx_jct_user_lookup'),
        ]

        verbose_name = 'Empire-Plan Link'
        verbose_name_plural = 'Empire-Plan Links'


class PlanningCX(UUIDModel, ChangeTrackedModel):
    user = models.ForeignKey('user.User', on_delete=models.CASCADE, related_name='cxs')
    cx_name = models.CharField(max_length=200)

    # cx data json
    cx_data = models.JSONField(default=dict)
    schema_version = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)], db_index=True)

    objects: models.Manager['PlanningCX'] = models.Manager()

    class Meta:
        db_table = 'prunplanner_planning_cxs'
        verbose_name = 'CX Preference'
        verbose_name_plural = 'CX Preferences'


class PlanningShared(UUIDModel, ChangeTrackedModel):
    user = models.ForeignKey('user.User', on_delete=models.CASCADE, related_name='shared_plans')
    plan = models.OneToOneField('PlanningPlan', on_delete=models.CASCADE, related_name='share_link')

    view_count = models.PositiveIntegerField(default=0)

    objects: models.Manager['PlanningShared'] = models.Manager()

    def __str__(self) -> str:
        return f'Shared: {self.plan.plan_name} (by {self.user.username})'

    class Meta:
        db_table = 'prunplanner_planning_shares'
        verbose_name = 'Shared Plan'
        verbose_name_plural = 'Shared Plans'
        constraints = [models.UniqueConstraint(fields=['user', 'plan'], name='unique_user_plan_share')]
