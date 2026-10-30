from core.models import ChangeTrackedModel, UUIDModel
from django.core.validators import MaxValueValidator, MinLengthValidator, MinValueValidator
from django.db import models
from django.utils import timezone


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

    # state
    empire_state = models.JSONField(default=dict, blank=True)
    needs_state_sync = models.BooleanField(default=False, db_index=True)

    # version of the configuration (name, faction, permits) for save conflicts; modified_at also moves on state syncs
    config_modified_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f'{self.empire_name} ({self.uuid})'

    class Meta:
        db_table = 'prunplanner_planning_empires'
        verbose_name = 'Empire'
        verbose_name_plural = 'Empires'

        # Note, on Postgres there are two GIN indexes generated:
        # idx_gin_empire_total -> gin (((empire_state -> \'empire_total\')) jsonb_path_ops)
        # idx_gin_plan_details -> gin ((empire_state -> \'plan_details\'))


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


class PlanVersionKind(models.TextChoices):
    SAVE = 'save'
    RESTORE = 'restore'
    BASELINE = 'baseline'
    ADMIN = 'admin'
    DRAFT = 'draft'


class SavedVersionManager(models.Manager['PlanningPlanVersion']):
    def get_queryset(self) -> models.QuerySet['PlanningPlanVersion']:
        return super().get_queryset().exclude(kind=PlanVersionKind.DRAFT)


class DraftVersionManager(models.Manager['PlanningPlanVersion']):
    def get_queryset(self) -> models.QuerySet['PlanningPlanVersion']:
        return super().get_queryset().filter(kind=PlanVersionKind.DRAFT)


class PlanningPlanVersion(UUIDModel):
    """A snapshot of a plan: one row per save, plus at most one autosave draft. Written by plan_version_service only."""

    plan = models.ForeignKey(PlanningPlan, on_delete=models.CASCADE, related_name='versions')
    user = models.ForeignKey('user.User', on_delete=models.CASCADE, related_name='plan_versions')

    kind = models.CharField(max_length=10, choices=PlanVersionKind.choices)
    name = models.CharField(max_length=80, blank=True)
    source_version = models.ForeignKey('self', on_delete=models.SET_NULL, related_name='+', null=True, blank=True)
    # drafts only: the plan's modified_at the edits started from
    base_modified_at = models.DateTimeField(null=True, blank=True)
    data_bytes = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # the snapshot
    plan_name = models.CharField(max_length=200)
    planet_natural_id = models.CharField(max_length=7, validators=[MinLengthValidator(7)])
    plan_permits_used = models.PositiveIntegerField(validators=[MaxValueValidator(3)])
    plan_cogc = models.CharField(max_length=20, choices=PlanningCOGCChoices.choices)
    plan_corphq = models.BooleanField(default=False)
    plan_data = models.JSONField(default=dict)
    schema_version = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    # first, so it's the default manager: the admin's inline formset validates its rows against that one, drafts too
    all_objects = models.Manager()
    objects = SavedVersionManager()
    drafts = DraftVersionManager()

    def __str__(self) -> str:
        return f'{self.plan_name} @ {self.created_at:%Y-%m-%d %H:%M} ({self.kind})'

    class Meta:
        db_table = 'prunplanner_planning_plan_versions'
        verbose_name = 'Plan Version'
        verbose_name_plural = 'Plan Versions'
        indexes = [models.Index(fields=['plan', '-created_at'], name='plan_version_newest_idx')]
        constraints = [
            models.UniqueConstraint(
                fields=['plan'], condition=models.Q(kind=PlanVersionKind.DRAFT), name='unique_plan_draft'
            ),
            models.CheckConstraint(
                condition=models.Q(kind=PlanVersionKind.DRAFT, base_modified_at__isnull=False)
                | (~models.Q(kind=PlanVersionKind.DRAFT) & models.Q(base_modified_at__isnull=True)),
                name='plan_version_base_only_on_draft',
            ),
            models.CheckConstraint(
                condition=~models.Q(kind=PlanVersionKind.DRAFT) | models.Q(name=''),
                name='plan_version_draft_unnamed',
            ),
        ]
