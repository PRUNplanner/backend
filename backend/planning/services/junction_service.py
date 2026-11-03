from collections.abc import Iterable
from uuid import UUID

from core.services.cache_manager import CacheManager
from django.db import transaction
from django.db.models import Case, Value, When
from planning.models import PlanningCX, PlanningEmpire, PlanningEmpirePlan, PlanningPlan
from planning.planning_cache_manager import PLANNING
from rest_framework import status
from user.models import User


class JunctionRejected(Exception):
    """A junction sync the user may not make; the viewset answers with `body` and `status_code`."""

    def __init__(self, body: dict[str, object], status_code: int) -> None:
        super().__init__(body)
        self.body = body
        self.status_code = status_code


def sync_empire_plans(user: User, links: dict[UUID, set[UUID]]) -> None:
    """Makes each given empire link exactly the given plans (empire uuid -> plan uuids)."""
    req_empire_uuids = set(links)
    req_plan_uuids = set().union(*links.values())

    owned_empire_uuids = set(
        PlanningEmpire.objects.filter(user=user, uuid__in=req_empire_uuids).values_list('uuid', flat=True)
    )
    owned_plan_uuids = set(
        PlanningPlan.objects.filter(user=user, uuid__in=req_plan_uuids).values_list('uuid', flat=True)
    )

    unauthorized_empires = req_empire_uuids - owned_empire_uuids
    unauthorized_plans = req_plan_uuids - owned_plan_uuids
    if unauthorized_empires or unauthorized_plans:
        raise JunctionRejected(
            {
                'error': 'Unauthorized or non-existend objects referenced',
                'invalid_empires': list(unauthorized_empires),
                'invalid_plans': list(unauthorized_plans),
            },
            status.HTTP_403_FORBIDDEN,
        )

    desired_pairs = {(e_uuid, p_uuid) for e_uuid, p_uuids in links.items() for p_uuid in p_uuids}

    with transaction.atomic():
        existing_links = PlanningEmpirePlan.objects.filter(user=user, empire_id__in=req_empire_uuids).values_list(
            'uuid', 'empire_id', 'plan_id'
        )
        # (empire_id, plan_id) -> junction uuid
        existing_map = {(e_id, p_id): jct_uuid for jct_uuid, e_id, p_id in existing_links}
        existing_pairs = set(existing_map)

        to_delete_uuids = [existing_map[pair] for pair in existing_pairs - desired_pairs]
        to_create_pairs = desired_pairs - existing_pairs

        if to_delete_uuids:
            PlanningEmpirePlan.objects.filter(uuid__in=to_delete_uuids).delete()
        if to_create_pairs:
            PlanningEmpirePlan.objects.bulk_create(
                [PlanningEmpirePlan(user=user, empire_id=e_id, plan_id=p_id) for e_id, p_id in to_create_pairs]
            )

        # bulk writes send no signals, so the cached plan and empire lists are bumped here
        if to_delete_uuids or to_create_pairs:
            CacheManager.invalidate_on_commit(PLANNING, user.id)


def sync_cx_empires(user: User, assignments: Iterable[tuple[UUID, list[UUID]]]) -> None:
    """Points each listed empire at its CX; every other empire of the user loses its CX."""
    assignments = list(assignments)
    cx_uuids = {cx_uuid for cx_uuid, _ in assignments}
    empire_list = [e_uuid for _, e_uuids in assignments for e_uuid in e_uuids]
    empire_uuids = set(empire_list)

    if PlanningCX.objects.filter(user=user, uuid__in=cx_uuids).count() != len(cx_uuids):
        raise JunctionRejected({'error': 'Invalid CX UUIDs detected.'}, status.HTTP_403_FORBIDDEN)
    if PlanningEmpire.objects.filter(user=user, uuid__in=empire_uuids).count() != len(empire_uuids):
        raise JunctionRejected({'error': 'Invalid Empire UUIDs detected.'}, status.HTTP_403_FORBIDDEN)
    if len(empire_list) != len(empire_uuids):
        raise JunctionRejected({'error': 'Duplicate empire assignment in request.'}, status.HTTP_400_BAD_REQUEST)

    update_conditions = [
        When(uuid=e_uuid, then=Value(cx_uuid)) for cx_uuid, e_uuids in assignments for e_uuid in e_uuids
    ]
    with transaction.atomic():
        # queryset update sends no signals, so the cached lists are bumped here
        PlanningEmpire.objects.filter(user=user).update(cx_id=Case(*update_conditions, default=None))
        CacheManager.invalidate_on_commit(PLANNING, user.id)
