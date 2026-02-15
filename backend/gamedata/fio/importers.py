from django.db import transaction

from gamedata.fio.schemas.fio_planet import (
    FIOPlanetCOGCProgramSchema,
    FIOPlanetProductionFeeSchema,
    FIOPlanetResourceSchema,
)
from gamedata.fio.services import get_fio_service
from gamedata.gamedata_cache_manager import GamedataCacheManager
from gamedata.models import (
    GameBuilding,
    GameBuildingCost,
    GameExchange,
    GameMaterial,
    GamePlanet,
    GamePlanetCOGCProgram,
    GamePlanetInfrastructureReport,
    GamePlanetProductionFee,
    GamePlanetResource,
    GamePlanetResourceTypeChoices,
    GameRecipe,
    GameRecipeInput,
    GameRecipeOutput,
)


def import_planet(planet_natural_id: str) -> bool:
    with get_fio_service() as fio:
        data = fio.get_planet(planet_natural_id)

    if not data:
        return False

    planet_instance = None
    import_error = None

    with transaction.atomic():
        try:
            # delete existing data, cascades children
            planet_instance, _created = GamePlanet.objects.update_or_create(
                planet_natural_id=data.planet_natural_id,
                defaults=data.model_dump(exclude={'resources', 'cogc_programs', 'production_fees'}),
            )

            # Get Material ticker map once
            material_map = GameMaterial.material_id_ticker_map()

            # Synchronize all 1:n relationships
            planet_sync_resources(planet_instance, data.resources, material_map)
            planet_sync_cogc_programs(planet_instance, data.cogc_programs)
            planet_sync_production_fees(planet_instance, data.production_fees)

            planet_instance.update_refresh_result()

            return True

        except Exception as exc:
            import_error = exc

    if import_error:
        planet_to_log = GamePlanet.objects.filter(planet_id=data.planet_id).first()
        if planet_to_log:
            planet_to_log.update_refresh_result(error=import_error)

    return False


def planet_sync_resources(planet: GamePlanet, resource_data: list[FIOPlanetResourceSchema], material_map: dict):

    existing_objs = {r.material_id: r for r in planet.resources.all()}

    seen_material_ids = set()
    to_create = []
    to_update = []

    for item in resource_data:
        m_id = item.material_id
        seen_material_ids.add(m_id)

        multiplier = 60.0 if item.resource_type == GamePlanetResourceTypeChoices.Gaseous else 70.0
        daily_ext = item.factor * multiplier
        ticker = material_map.get(m_id, '')

        if m_id in existing_objs:
            obj = existing_objs[m_id]
            obj.factor = item.factor
            obj.resource_type = item.resource_type
            obj.daily_extraction = daily_ext
            obj.material_ticker = ticker
            to_update.append(obj)
        else:
            to_create.append(
                GamePlanetResource(
                    planet=planet,
                    material_id=m_id,
                    factor=item.factor,
                    resource_type=item.resource_type,
                    daily_extraction=daily_ext,
                    material_ticker=ticker,
                )
            )

    if to_update:
        GamePlanetResource.objects.bulk_update(
            to_update, fields=['factor', 'resource_type', 'daily_extraction', 'material_ticker']
        )

    if to_create:
        GamePlanetResource.objects.bulk_create(to_create)

    planet.resources.exclude(material_id__in=seen_material_ids).delete()


def planet_sync_cogc_programs(planet: GamePlanet, cogc_data: list[FIOPlanetCOGCProgramSchema]):
    existing = {(p.program_type, p.start_epochms, p.end_epochms): p.pk for p in planet.cogc_programs.all()}

    ids_to_keep = []
    to_create = []

    for item in cogc_data:
        key = (item.program_type, item.start_epochms, item.end_epochms)

        if key in existing:
            ids_to_keep.append(existing[key])
        else:
            to_create.append(GamePlanetCOGCProgram(planet=planet, **item.model_dump()))

    if to_create:
        new_objs = GamePlanetCOGCProgram.objects.bulk_create(to_create)
        ids_to_keep.extend([obj.pk for obj in new_objs])

    if not ids_to_keep:
        planet.cogc_programs.all().delete()
    else:
        planet.cogc_programs.exclude(pk__in=ids_to_keep).delete()


def planet_sync_production_fees(planet: GamePlanet, fee_data: list[FIOPlanetProductionFeeSchema]):
    existing = {(f.category, f.workforce_level): f for f in planet.production_fees.all()}

    to_create = []
    to_update = []
    ids_to_keep = []

    for item in fee_data:
        key = (item.category, item.workforce_level)

        if key in existing:
            obj = existing[key]
            obj.fee_amount = item.fee_amount
            obj.fee_currency = item.fee_currency
            to_update.append(obj)
            ids_to_keep.append(obj.pk)
        else:
            to_create.append(GamePlanetProductionFee(planet=planet, **item.model_dump()))

    if to_update:
        GamePlanetProductionFee.objects.bulk_update(to_update, ['fee_amount', 'fee_currency'])

    if to_create:
        returned_objs = GamePlanetProductionFee.objects.bulk_create(to_create)
        ids_to_keep.extend([obj.pk for obj in returned_objs])

    if not ids_to_keep:
        planet.production_fees.all().delete()
    else:
        planet.production_fees.exclude(pk__in=ids_to_keep).delete()


def import_all_planets() -> bool:
    # fetch all data from fio
    with get_fio_service() as fio:
        planets = fio.get_all_planets()

    fetched_planet_natural_ids = [p.planet_natural_id for p in planets]

    with transaction.atomic():
        # delete all existing that we have
        GamePlanet.objects.filter(planet_natural_id__in=fetched_planet_natural_ids).delete()

        # bulk planet creation
        planet_objs = [
            GamePlanet(**p.model_dump(exclude={'resources', 'cogc_programs', 'production_fees'})) for p in planets
        ]
        GamePlanet.objects.bulk_create(planet_objs)

        # create a map of planets by their planet_natural_id
        planet_map = {
            p.planet_natural_id: p
            for p in GamePlanet.objects.filter(planet_natural_id__in=[p.planet_natural_id for p in planets])
        }

        material_ticker_map = GameMaterial.material_id_ticker_map()

        # resources
        resource_objs = [
            GamePlanetResource(
                planet=planet_map[p.planet_natural_id],
                **r.model_dump(),
                daily_extraction=r.factor * 60.0
                if r.resource_type == GamePlanetResourceTypeChoices.Gaseous
                else r.factor * 70.0,
                material_ticker=material_ticker_map[r.material_id]
                if r.material_id in material_ticker_map.keys()
                else None,
            )
            for p in planets
            for r in p.resources
        ]

        fee_objs = [
            GamePlanetProductionFee(planet=planet_map[p.planet_natural_id], **r.model_dump())
            for p in planets
            for r in p.production_fees
        ]

        program_objs = [
            GamePlanetCOGCProgram(planet=planet_map[p.planet_natural_id], **r.model_dump())
            for p in planets
            for r in p.cogc_programs
        ]

        GamePlanetResource.objects.bulk_create(resource_objs, ignore_conflicts=True)
        GamePlanetProductionFee.objects.bulk_create(fee_objs, ignore_conflicts=True)
        GamePlanetCOGCProgram.objects.bulk_create(program_objs, ignore_conflicts=True)

    GamedataCacheManager.delete_pattern('*planet*')

    return True


def import_planet_infrastructure(planet_natural_id: str) -> bool:
    with get_fio_service() as fio:
        data = fio.get_planet_infrastructure(planet_natural_id)

    if not data:
        return False

    PERIODS_TO_KEEP = 10

    last_periods = sorted(data.infrastructure_reports, key=lambda x: x.simulation_period, reverse=True)[
        :PERIODS_TO_KEEP
    ]

    # get planet instance
    planet: GamePlanet | None = GamePlanet.objects.get(planet_natural_id=planet_natural_id)

    if not planet:
        return False

    fetched_periods = {r.simulation_period for r in last_periods}
    existing_periods = set(
        planet.popr_reports.filter(simulation_period__in=fetched_periods).values_list('simulation_period', flat=True)
    )

    # create missing reports
    to_create = []
    for r in last_periods:
        if r.simulation_period not in existing_periods:
            to_create.append(GamePlanetInfrastructureReport(planet=planet, **r.model_dump()))

    if to_create:
        GamePlanetInfrastructureReport.objects.bulk_create(to_create)

    # cleanup: delete all that are not in our top 10 periods
    if fetched_periods:
        min_period = min(fetched_periods)
        planet.popr_reports.filter(simulation_period__lt=min_period).delete()

    GamedataCacheManager.delete(GamedataCacheManager.key_planet_popr(planet_natural_id))

    return True


def import_all_exchanges() -> bool:
    with get_fio_service() as fio:
        exchanges = fio.get_all_exchanges()

    with transaction.atomic():
        exchange_objs = [GameExchange(**e.model_dump()) for e in exchanges]

        GameExchange.objects.bulk_create(
            exchange_objs,
            update_conflicts=True,
            unique_fields=['ticker_id'],
            update_fields=[
                'mm_buy',
                'mm_sell',
                'price_average',
                'ask',
                'bid',
                'ask_count',
                'bid_count',
                'supply',
                'demand',
            ],
        )

    GamedataCacheManager.delete(GamedataCacheManager.key_exchange_list())

    return True


def import_all_recipes() -> tuple[int, int, int]:
    with get_fio_service() as fio:
        recipes = fio.get_all_recipes()

    with transaction.atomic():
        GameRecipe.objects.all().delete()

        recipe_objs = [GameRecipe(**r.model_dump(exclude={'inputs', 'outputs'})) for r in recipes]
        GameRecipe.objects.bulk_create(recipe_objs, ignore_conflicts=True)

        # Map created recipes by standard_recipe_name
        recipe_map = {
            r.standard_recipe_name: r
            for r in GameRecipe.objects.filter(standard_recipe_name__in=[r.standard_recipe_name for r in recipes])
        }

        input_objs = [
            GameRecipeInput(
                recipe=recipe_map[r.standard_recipe_name],
                material_ticker=i.material_ticker,
                material_amount=i.material_amount,
            )
            for r in recipes
            for i in r.inputs
        ]

        output_objs = [
            GameRecipeOutput(
                recipe=recipe_map[r.standard_recipe_name],
                material_ticker=o.material_ticker,
                material_amount=o.material_amount,
            )
            for r in recipes
            for o in r.outputs
        ]

        GameRecipeInput.objects.bulk_create(input_objs, ignore_conflicts=True)

        GameRecipeOutput.objects.bulk_create(output_objs, ignore_conflicts=True)

    GamedataCacheManager.delete(GamedataCacheManager.key_recipe_list())

    return len(recipe_objs), len(input_objs), len(output_objs)


def import_all_materials() -> tuple[int, int]:
    with get_fio_service() as fio:
        fio_materials = fio.get_all_materials()

    material_objs = [GameMaterial(**mat.model_dump()) for mat in fio_materials]

    with transaction.atomic():
        deleted_count, _ = GameMaterial.objects.all().delete()

        GameMaterial.objects.bulk_create(material_objs)

    GamedataCacheManager.delete(GamedataCacheManager.key_material_list())

    return deleted_count, len(material_objs)


def import_all_buildings() -> tuple[int, int]:
    with get_fio_service() as fio:
        fio_buildings = fio.get_all_buildings()

        fetched_ids = [b.building_id for b in fio_buildings]

    with transaction.atomic():
        # Delete all existing buildings
        deleted_count, _ = GameBuilding.objects.all().delete()

        # Prepare GameBuilding objects
        building_objs = [GameBuilding(**b.model_dump(exclude={'building_costs'})) for b in fio_buildings]

        GameBuilding.objects.bulk_create(building_objs, ignore_conflicts=True)

        # Map created buildings by ID
        building_map = {b.building_id: b for b in GameBuilding.objects.filter(building_id__in=fetched_ids)}

        # Prepare GameBuildingCost objects
        cost_objs = [
            GameBuildingCost(building=building_map[b.building_id], **c.model_dump())
            for b in fio_buildings
            for c in b.building_costs
        ]

        GameBuildingCost.objects.bulk_create(cost_objs, ignore_conflicts=True)

    return len(building_objs), len(cost_objs)
