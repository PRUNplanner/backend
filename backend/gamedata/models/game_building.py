import uuid

from django.db import models

from .model_constants import habitations


class GameBuildingExpertiseChoices(models.TextChoices):
    AGRICULTURE = 'AGRICULTURE'
    CHEMISTRY = 'CHEMISTRY'
    CONSTRUCTION = 'CONSTRUCTION'
    ELECTRONICS = 'ELECTRONICS'
    FOOD_INDUSTRIES = 'FOOD_INDUSTRIES'
    MANUFACTURING = 'MANUFACTURING'
    METALLURGY = 'METALLURGY'
    RESOURCE_EXTRACTION = 'RESOURCE_EXTRACTION'
    FUEL_REFINING = 'FUEL_REFINING'


class GameBuildingTypeChoices(models.TextChoices):
    PLANETARY = 'PLANETARY'
    INFRASTRUCTURE = 'INFRASTRUCTURE'
    PRODUCTION = 'PRODUCTION'


class GameBuilding(models.Model):
    building_id = models.CharField(primary_key=True, max_length=32)
    building_name = models.CharField(max_length=255)
    building_ticker = models.CharField(max_length=3, db_index=True)
    expertise = models.CharField(  # noqa: DJ001
        max_length=50,
        choices=GameBuildingExpertiseChoices.choices,
        blank=True,
        default='',
        db_index=True,
        null=True,
    )
    pioneers = models.PositiveIntegerField()
    settlers = models.PositiveIntegerField()
    technicians = models.PositiveIntegerField()
    engineers = models.PositiveIntegerField()
    scientists = models.PositiveIntegerField()
    area_cost = models.PositiveIntegerField()

    building_type = models.CharField(
        max_length=50,
        choices=GameBuildingTypeChoices.choices,
        default='PRODUCTION',
    )

    class Meta:
        db_table = 'prunplanner_game_buildings'
        verbose_name = 'Building'
        verbose_name_plural = 'Buildings'

    def __str__(self) -> str:
        return f'{self.building_ticker} ({self.building_name})'

    @property
    def habitations(self) -> dict[str, int] | None:
        if self.building_ticker in habitations.keys():
            habitation_data = habitations[self.building_ticker].copy()
            habitation_data.pop('area', None)
            return habitation_data
        else:
            return None

    objects: models.Manager['GameBuilding'] = models.Manager()


class GameBuildingCost(models.Model):
    building_cost_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    building = models.ForeignKey(GameBuilding, related_name='costs', on_delete=models.CASCADE)
    material_ticker = models.CharField(max_length=3, blank=False, db_index=True)
    material_amount = models.PositiveIntegerField()

    objects: models.Manager['GameBuildingCost'] = models.Manager()

    class Meta:
        db_table = 'prunplanner_game_buildings_costs'
        verbose_name = 'Building Cost'
        verbose_name_plural = 'Building Costs'

    def __str__(self) -> str:
        return f'{self.building} ({self.material_amount}x{self.material_ticker})'
