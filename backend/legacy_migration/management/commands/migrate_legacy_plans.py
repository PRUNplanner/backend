import uuid
from typing import Any

from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.db.models import QuerySet
from legacy_migration.models.legacy_planning import (
    LegacyBaseplanner,
    LegacyCX,
    LegacyEmpire,
    LegacyEmpirePlanJunction,
    LegacyShared,
)
from planning.models import PlanningCX, PlanningEmpire, PlanningEmpirePlan, PlanningPlan, PlanningShared
from planning.schemas.planning_cx_data import CXExchangeTickerPreferences_Legacy, CXExchangeTickerPreferences_V1
from planning.schemas.planning_plan_data import PlanningPlanData_Legacy, PlanningPlanData_V1
from pydantic import ValidationError


def import_legacy_shares(legacy_shares: QuerySet[LegacyShared], legacy_plan_id_uuid_map: dict[int, uuid.UUID]) -> int:

    batch_shared: list[PlanningShared] = []
    count: int = 0

    for shared in legacy_shares:
        new_shared = PlanningShared(
            uuid=shared.uuid,
            user_id=shared.user_id,
            plan_id=legacy_plan_id_uuid_map[shared.baseplanner_id],
            view_count=shared.view_count,
            created_at=shared.created_date,
            modified_at=shared.created_date,
        )

        batch_shared.append(new_shared)
        count += 1

        if len(batch_shared) >= 500:
            PlanningShared.objects.bulk_create(batch_shared, ignore_conflicts=True)
            batch_shared.clear()

    # create remaining plans outside of executed batches
    if batch_shared:
        PlanningShared.objects.bulk_create(batch_shared, ignore_conflicts=True)
        batch_shared.clear()

    return count


def import_legacy_plans(legacy_plans: QuerySet[LegacyBaseplanner]) -> dict[int, uuid.UUID]:
    legacy_plan_id_uuid_map: dict = {}
    batch_plans: list[PlanningPlan] = []

    for plan in legacy_plans:
        try:
            # parse legacy data
            parsed_plan_data = PlanningPlanData_Legacy.model_validate_json(plan.baseplanner_data)

            # store legacy plan id to uuid map
            legacy_plan_id_uuid_map[plan.user_baseplanner_id] = plan.uuid

            # transform to new model

            plan_data = PlanningPlanData_V1(
                experts=[
                    PlanningPlanData_V1.PlanningPlanData_V1_Experts(**e.model_dump())
                    for e in parsed_plan_data.planet.experts
                ],
                workforce=[
                    PlanningPlanData_V1.PlanningPlanData_V1_Workforce(**w.model_dump())
                    for w in parsed_plan_data.planet.workforce
                ],
                infrastructure=[
                    PlanningPlanData_V1.PlanningPlanData_V1_Infrastructure(**i.model_dump())
                    for i in parsed_plan_data.infrastructure
                ],
                buildings=[
                    PlanningPlanData_V1.PlanningPlanData_V1_Building(**b.model_dump())
                    for b in parsed_plan_data.buildings
                ],
            )

            # create new django model records
            new_plan = PlanningPlan(
                uuid=plan.uuid,
                user_id=plan.user_id,
                plan_name=plan.name,
                planet_natural_id=plan.planet_id,
                plan_permits_used=parsed_plan_data.planet.permits,
                plan_cogc=parsed_plan_data.planet.cogc if parsed_plan_data.planet.cogc else '---',
                plan_corphq=parsed_plan_data.planet.corphq,
                plan_data=plan_data.model_dump(),
            )

            batch_plans.append(new_plan)

        except Exception as e:
            print(e)
            print(f'Failed to parse: {plan.uuid}')

        if len(batch_plans) >= 500:
            PlanningPlan.objects.bulk_create(batch_plans, ignore_conflicts=True)
            batch_plans.clear()

    # create remaining plans outside of executed batches
    if batch_plans:
        PlanningPlan.objects.bulk_create(batch_plans, ignore_conflicts=True)
        batch_plans.clear()

    return legacy_plan_id_uuid_map


def import_legacy_empires(legacy_empires: QuerySet[LegacyEmpire]) -> tuple[dict[int, uuid.UUID], dict[int, uuid.UUID]]:
    legacy_empire_id_uuid_map: dict = {}
    legacy_cx_id_empire_id_map: dict = {}

    batch_empires: list[PlanningEmpire] = []

    for empire in legacy_empires:
        # store legacy id to uuid map
        legacy_empire_id_uuid_map[empire.user_empire_id] = empire.uuid

        if empire.cx_id:
            legacy_cx_id_empire_id_map[empire.cx_id] = empire.uuid

        new_empire = PlanningEmpire(
            uuid=empire.uuid,
            user_id=empire.user_id,
            empire_name=empire.name,
            empire_faction=empire.faction,
            empire_permits_used=empire.permits_used,
            empire_permits_total=empire.permits_total,
        )

        batch_empires.append(new_empire)

        if len(batch_empires) >= 500:
            PlanningEmpire.objects.bulk_create(batch_empires, ignore_conflicts=True)
            batch_empires.clear()

    # create remaining empires outside of executed batches
    if batch_empires:
        PlanningEmpire.objects.bulk_create(batch_empires, ignore_conflicts=True)
        batch_empires.clear()

    return legacy_empire_id_uuid_map, legacy_cx_id_empire_id_map


def import_legacy_empire_plan_jct(
    legacy_empire_plan: list[tuple[int, int]],
    legacy_empire_id_uuid_map: dict[int, uuid.UUID],
    legacy_plan_id_uuid_map: dict[int, uuid.UUID],
) -> None:
    empire_data = PlanningEmpire.objects.values_list('uuid', 'user_id')
    empire_dict: dict[uuid.UUID, int] = dict(empire_data)

    batch_jct: list[PlanningEmpirePlan] = []

    for jct in legacy_empire_plan:
        new_jct = PlanningEmpirePlan(
            user_id=empire_dict[legacy_empire_id_uuid_map[jct[0]]],
            empire_id=legacy_empire_id_uuid_map[jct[0]],
            plan_id=legacy_plan_id_uuid_map[jct[1]],
        )

        batch_jct.append(new_jct)

        if len(batch_jct) >= 500:
            PlanningEmpirePlan.objects.bulk_create(batch_jct, ignore_conflicts=True)
            batch_jct.clear()

    if batch_jct:
        PlanningEmpirePlan.objects.bulk_create(batch_jct, ignore_conflicts=True)
        batch_jct.clear()


def import_legacy_cx(cxs: QuerySet[LegacyCX], legacy_cx_id_empire_map: dict[int, uuid.UUID]) -> None:
    for cx in cxs:
        try:
            legacy_data = CXExchangeTickerPreferences_Legacy.model_validate_json(cx.cx_data)
            cleaned_dict = legacy_data.model_dump()
            parsed_data = CXExchangeTickerPreferences_V1(**cleaned_dict)

            try:
                new_cx = PlanningCX.objects.create(
                    user_id=cx.user_id, uuid=cx.uuid, cx_name=cx.name, cx_data=parsed_data.model_dump()
                )

                empire_uuid: uuid.UUID | None = legacy_cx_id_empire_map.get(cx.user_cx_id)
                if empire_uuid:
                    PlanningEmpire.objects.filter(uuid=empire_uuid).update(cx=new_cx)
            except IntegrityError:
                pass
        except ValidationError as e:
            for error in e.errors():
                print(f'Error in location: {error["loc"]}')
                print(f'Input that failed: {error["input"]}')
        except Exception as e:
            print(e)
            print(cx.uuid)


class Command(BaseCommand):
    help = 'Migrate plans from the legacy database to the new plan model incl. schema upgrades'

    def add_arguments(self, parser):
        parser.add_argument('--user', type=int, help='Migrate data of specific user id')

    def handle(self, *args: Any, **options: Any) -> None:

        user_id: int | None = options['user']

        if user_id:
            legacy_plans = LegacyBaseplanner.objects.using('legacy').filter(user_id=user_id)
            legacy_empire = LegacyEmpire.objects.using('legacy').filter(user_id=user_id)
            legacy_cxs = LegacyCX.objects.using('legacy').filter(user_id=user_id)
            legacy_shares = LegacyShared.objects.using('legacy').filter(user_id=user_id)
        else:
            legacy_plans = LegacyBaseplanner.objects.using('legacy').all()
            legacy_empire = LegacyEmpire.objects.using('legacy').all()
            legacy_cxs = LegacyCX.objects.using('legacy').all()
            legacy_shares = LegacyShared.objects.using('legacy').all()

        # plans and empires
        legacy_plan_id_uuid_map = import_legacy_plans(legacy_plans)
        legacy_empire_id_uuid_map, legacy_cx_id_empire_id_map = import_legacy_empires(legacy_empire)

        legacy_jct = (
            LegacyEmpirePlanJunction.objects.using('legacy')
            .filter(empire_id__in=legacy_empire_id_uuid_map.keys())
            .values('empire_id', 'baseplanner_id')
        )
        legacy_jct_tuples = [(row['empire_id'], row['baseplanner_id']) for row in legacy_jct]

        # jct
        import_legacy_empire_plan_jct(legacy_jct_tuples, legacy_empire_id_uuid_map, legacy_plan_id_uuid_map)

        import_legacy_cx(legacy_cxs, legacy_cx_id_empire_id_map)

        # shares
        share_count = import_legacy_shares(legacy_shares, legacy_plan_id_uuid_map)

        stats = (
            f'Legacy planning. Plans: {len(legacy_plan_id_uuid_map.keys())}, '
            f'Empires: {len(legacy_empire_id_uuid_map.keys())}, '
            f'JCT: {len(legacy_jct_tuples)}, CX: {len(legacy_cxs)}, '
            f'Shares: {share_count}'
        )

        self.stdout.write(self.style.SUCCESS(stats))
