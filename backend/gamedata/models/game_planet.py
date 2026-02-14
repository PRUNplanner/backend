from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, TypeVar, cast

from core.models import CeleryAutomationModel
from django.db import models, transaction
from django.db.models import Manager, QuerySet
from django.utils import timezone

from gamedata.models.game_building import GameBuildingExpertiseChoices
from gamedata.models.game_material import GameMaterial


def queryset_gameplanet() -> QuerySet:
    from django.db.models import OuterRef, Subquery

    now_ms = int(timezone.now().timestamp() * 1000)

    active_program_sub = (
        GamePlanetCOGCProgram.objects.filter(
            planet=OuterRef('pk'),
            start_epochms__lte=now_ms,
            end_epochms__gte=now_ms,
        )
        .order_by()
        .values('program_type')[:1]
    )

    return GamePlanet.objects.prefetch_related('cogc_programs', 'resources').annotate(
        active_cogc_program_type=Subquery(active_program_sub)
    )


class GamePlanetFactionCodeChoices(models.TextChoices):
    NC = 'NC'
    CI = 'CI'
    AI = 'AI'
    IC = 'IC'


class GamePlanetFactionChoices(models.TextChoices):
    NEO_Charter_Exploration = 'NEO Charter Exploration'
    Castillo_Ito_Mercantile = 'Castillo-Ito Mercantile'
    Antares_Initiative = 'Antares Initiative'
    Insitor_Cooperative = 'Insitor Cooperative'


class GamePlanetCurrencyCodeChoices(models.TextChoices):
    NCC = 'NCC'
    CIS = 'CIS'
    AIC = 'AIC'
    ICA = 'ICA'


class GamePlanetCurrencyNameChoices(models.TextChoices):
    NCE_Coupons = 'NCE Coupons'
    Sol = 'Sol'
    Martian_Coin = 'Martian Coin'
    Austral = 'Austral'


class GamePlanetCOGCStatusChoices(models.TextChoices):
    Active = 'ACTIVE'
    Planned = 'PLANNED'
    On_Strike = 'ON_STRIKE'


class GamePlanetResourceTypeChoices(models.TextChoices):
    Gaseous = 'GASEOUS'
    Liquid = 'LIQUID'
    Mineral = 'MINERAL'


class GamePlanetWorkforceLevelChoices(models.TextChoices):
    Engineer = 'ENGINEER'
    Technician = 'TECHNICIAN'
    Scientist = 'SCIENTIST'
    Settler = 'SETTLER'
    Pioneer = 'PIONEER'


class GamePlanetEnvironmentChoices(models.TextChoices):
    NORMAL = 'NORMAL'
    HIGH = 'HIGH'
    LOW = 'LOW'


class GamePlanetCOGCProgramChoices(models.TextChoices):
    Agriculture = 'ADVERTISING_AGRICULTURE'
    Chemistry = 'ADVERTISING_CHEMISTRY'
    Construction = 'ADVERTISING_CONSTRUCTION'
    Electronics = 'ADVERTISING_ELECTRONICS'
    Food_Industries = 'ADVERTISING_FOOD_INDUSTRIES'
    Fuel_Refining = 'ADVERTISING_FUEL_REFINING'
    Manufacturing = 'ADVERTISING_MANUFACTURING'
    Metallurgy = 'ADVERTISING_METALLURGY'
    Resource_Extraction = 'ADVERTISING_RESOURCE_EXTRACTION'
    Workforce_Pioneers = 'WORKFORCE_PIONEERS'
    Workforce_Settlers = 'WORKFORCE_SETTLERS'
    Workforce_Technicians = 'WORKFORCE_TECHNICIANS'
    Workforce_Engineers = 'WORKFORCE_ENGINEERS'
    Workforce_Scientists = 'WORKFORCE_SCIENTISTS'


M = TypeVar('M', bound=models.Model)


class GamePlanet(CeleryAutomationModel):
    planet_id = models.CharField(primary_key=True, max_length=32, editable=False)
    planet_natural_id = models.CharField(db_index=True)
    planet_name = models.CharField(blank=True, default='')
    system_id = models.CharField(db_index=True, max_length=32)

    magnetic_field = models.FloatField()
    mass = models.FloatField()
    mass_earth = models.FloatField()
    orbit_semimajor_axis = models.FloatField()
    orbit_eccentricity = models.FloatField()
    orbit_inclination = models.FloatField()
    orbit_right_ascension = models.FloatField()
    orbit_periapsis = models.FloatField()
    orbit_index = models.IntegerField()

    radiation = models.FloatField()
    radius = models.FloatField()
    sunlight = models.FloatField()

    surface = models.BooleanField(db_index=True)
    gravity = models.FloatField()
    pressure = models.FloatField()
    temperature = models.FloatField()
    fertility = models.FloatField()

    gravity_type = models.CharField(
        max_length=10,
        choices=GamePlanetEnvironmentChoices.choices,
        default=GamePlanetEnvironmentChoices.NORMAL,
        db_index=True,
    )
    pressure_type = models.CharField(
        max_length=10,
        choices=GamePlanetEnvironmentChoices.choices,
        default=GamePlanetEnvironmentChoices.NORMAL,
        db_index=True,
    )
    temperature_type = models.CharField(
        max_length=10,
        choices=GamePlanetEnvironmentChoices.choices,
        default=GamePlanetEnvironmentChoices.NORMAL,
        db_index=True,
    )
    fertility_type = models.BooleanField(db_index=True, default=False)

    has_localmarket = models.BooleanField(db_index=True)
    has_chamberofcommerce = models.BooleanField(db_index=True)
    has_warehouse = models.BooleanField(db_index=True)
    has_administrationcenter = models.BooleanField(db_index=True)
    has_shipyard = models.BooleanField(db_index=True)

    faction_code = models.CharField(max_length=2, blank=True, null=True, choices=GamePlanetFactionCodeChoices.choices)  # noqa: DJ001
    faction_name = models.CharField(max_length=50, blank=True, null=True, choices=GamePlanetFactionChoices.choices)  # noqa: DJ001
    currency_code = models.CharField(max_length=3, blank=True, null=True, choices=GamePlanetCurrencyCodeChoices.choices)  # noqa: DJ001
    currency_name = models.CharField(  # noqa: DJ001
        max_length=50, blank=True, null=True, choices=GamePlanetCurrencyNameChoices.choices
    )

    base_localmarket_fee = models.FloatField()
    localmarket_fee_factor = models.FloatField()
    warehouse_fee = models.FloatField()
    establishment_fee = models.FloatField()

    population_id = models.CharField(db_index=True, max_length=32)
    cogc_program_status = models.CharField(  # noqa: DJ001
        max_length=10, blank=True, null=True, choices=GamePlanetCOGCStatusChoices.choices
    )

    def replace_related(
        self,
        related_name: str,
        model_cls: type[M],
        new_data: list[dict[str, Any]],
    ) -> None:
        with transaction.atomic():
            manager = cast(Manager[M], getattr(self, related_name))
            manager.all().delete()
            model_cls._default_manager.bulk_create([model_cls(parent=self, **data) for data in new_data])

    objects: models.Manager[GamePlanet] = models.Manager()

    if TYPE_CHECKING:
        resources: models.QuerySet[GamePlanetResource]
        cogc_programs: models.QuerySet[GamePlanetCOGCProgram]
        production_fees: models.QuerySet[GamePlanetProductionFee]

    # Automation Retry Delay
    RETRY_DELAY_MINUTES = 30

    def __str__(self) -> str:
        return self.planet_natural_id

    class Meta:
        db_table = 'prunplanner_game_planets'
        verbose_name = 'Planet'
        verbose_name_plural = 'Planets'


class GamePlanetResource(models.Model):
    planet_resource_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    planet = models.ForeignKey(GamePlanet, related_name='resources', on_delete=models.CASCADE)

    material_id = models.CharField(db_index=True, max_length=32)
    resource_type = models.CharField(max_length=10, choices=GamePlanetResourceTypeChoices.choices)
    factor = models.FloatField()
    daily_extraction = models.FloatField(default=0)
    material_ticker = models.CharField(default=None, blank=True, max_length=3, db_index=True)

    objects: models.Manager[GamePlanetResource] = models.Manager()

    class Meta:
        db_table = 'prunplanner_game_planet_resources'
        verbose_name = 'Planet Resource'
        verbose_name_plural = 'Planet Resources'

        constraints = [
            models.UniqueConstraint(
                fields=['planet', 'material_id'],
                name='unique_planet_material_resource'
            )
        ]

    def __str__(self) -> str:
        return f'{self.material_ticker} @ {self.planet}'

    # Save override
    def save(self, *args: Any, **kwargs: Any) -> None:
        self.daily_extraction = self.calculate_daily_extraction()
        self.material_ticker = self.get_material_ticker()
        super().save(*args, **kwargs)

    def calculate_daily_extraction(self) -> float:
        return self.factor * 60.0 if self.resource_type == GamePlanetResourceTypeChoices.Gaseous else self.factor * 70.0

    def get_material_ticker(self) -> str | None:
        material = GameMaterial.objects.filter(material_id=self.material_id).first()
        return material.ticker if material else None


class GamePlanetProductionFee(models.Model):
    planet_production_fee_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    planet = models.ForeignKey(GamePlanet, related_name='production_fees', on_delete=models.CASCADE)

    category = models.CharField(max_length=50, choices=GameBuildingExpertiseChoices.choices)
    workforce_level = models.CharField(max_length=10, choices=GamePlanetWorkforceLevelChoices.choices)
    fee_amount = models.FloatField()
    fee_currency = models.CharField(max_length=3, choices=GamePlanetCurrencyCodeChoices.choices)

    objects: models.Manager[GamePlanetProductionFee] = models.Manager()

    class Meta:
        db_table = 'prunplanner_game_planet_production_fees'
        verbose_name = 'Planet Production Fee'
        verbose_name_plural = 'Planet Production Fees'

        constraints = [
            models.UniqueConstraint(
                fields=['planet', 'category', 'workforce_level'],
                name='unique_planet_fee_category_level'
            )
        ]

    def __str__(self) -> str:
        return f'{self.category}, {self.workforce_level} @ {self.planet}'


class GamePlanetCOGCProgram(models.Model):
    planet_cogc_program_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    planet = models.ForeignKey(GamePlanet, related_name='cogc_programs', on_delete=models.CASCADE)

    program_type = models.CharField(  # noqa: DJ001
        max_length=50, choices=GamePlanetCOGCProgramChoices.choices, blank=True, null=True, default=None, db_index=True
    )
    start_epochms = models.BigIntegerField(db_index=True)
    end_epochms = models.BigIntegerField(db_index=True)

    objects: models.Manager[GamePlanetCOGCProgram] = models.Manager()

    class Meta:
        db_table = 'prunplanner_game_planet_cogc_programs'
        verbose_name = 'Planet COGC Program'
        verbose_name_plural = 'Planet COGC Programs'

        constraints = [
            models.UniqueConstraint(
                fields=['planet', 'program_type', 'start_epochms', 'end_epochms'],
                name='unique_planet_cogc_window'
            )
        ]

    def __str__(self) -> str:
        return f'{self.program_type} ({self.start_epochms}-{self.end_epochms}) @ {self.planet}'
