from django.db import transaction

from gamedata.fio.services import get_fio_service
from gamedata.gamedata_cache_manager import GamedataCacheManager
from gamedata.models import (
    GameBuilding,
    GameBuildingCost,
    GameExchange,
    GameMaterial,
    GamePlanet,
    GamePlanetCOGCProgram,
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

    with transaction.atomic():
        try:
            # delete existing data, cascades children
            new_planet, _created = GamePlanet.objects.update_or_create(
                planet_natural_id=data.planet_natural_id,
                defaults=data.model_dump(exclude={'resources', 'cogc_programs', 'production_fees'}),
            )

            # delete related manually, as cascade is not riggered
            new_planet.resources.all().delete()
            new_planet.cogc_programs.all().delete()
            new_planet.production_fees.all().delete()

            # prepare related objects
            material_ticker_map = GameMaterial.material_id_ticker_map()

            # cogc programs
            cogc_programs = [GamePlanetCOGCProgram(planet=new_planet, **p.model_dump()) for p in data.cogc_programs]

            # resources
            resources = []
            for p in data.resources:
                # multiplier logic
                multiplier = 60.0 if p.resource_type == GamePlanetResourceTypeChoices.Gaseous else 70.0

                resources.append(
                    GamePlanetResource(
                        planet=new_planet,
                        **p.model_dump(),
                        daily_extraction=p.factor * multiplier,
                        material_ticker=material_ticker_map.get(p.material_id),
                    )
                )

            # fees
            fees = [GamePlanetProductionFee(planet=new_planet, **p.model_dump()) for p in data.production_fees]

            # bulk inserts
            GamePlanetCOGCProgram.objects.bulk_create(cogc_programs, ignore_conflicts=True)
            GamePlanetResource.objects.bulk_create(resources, ignore_conflicts=True)
            GamePlanetProductionFee.objects.bulk_create(fees, ignore_conflicts=True)

            new_planet.update_refresh_result()

            return True
        except Exception as exc:
            new_planet.update_refresh_result(error=exc)
            return True


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
