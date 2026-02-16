from typing import Any

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from planning.models import PlanningCX, PlanningEmpire, PlanningEmpirePlan, PlanningPlan
from planning.planning_cache_manager import PlanningCacheManager


@receiver([post_save, post_delete], sender=PlanningPlan, dispatch_uid='planning_invalidate_plan_caches')
def invalidate_plan_caches(sender: type[PlanningPlan], instance: PlanningPlan, **kwargs: Any) -> None:
    # get ids without additional db lookups
    user_id: int = instance.user_id  # type: ignore
    plan_uuid = instance.uuid

    def clear_cache():
        # lists
        PlanningCacheManager.delete(PlanningCacheManager.key_for_plan_list(user_id))
        PlanningCacheManager.delete(PlanningCacheManager.key_for_empire_list(user_id))
        PlanningCacheManager.delete_pattern(f'*{user_id}:empire:retrieve*')

        # details
        PlanningCacheManager.delete(PlanningCacheManager.key_plan_retrieve(user_id, plan_uuid))

    transaction.on_commit(clear_cache)


@receiver([post_save, post_delete], sender=PlanningEmpire, dispatch_uid='planning_invalidate_empire_caches')
def invalidate_empire_caches(sender: type[PlanningEmpire], instance: PlanningEmpire, **kwargs: Any) -> None:
    # get ids without additional db lookups
    user_id: int = instance.user_id  # type: ignore
    empire_uuid = instance.uuid

    def clear_cache():
        # lists
        PlanningCacheManager.delete(PlanningCacheManager.key_for_plan_list(user_id))
        PlanningCacheManager.delete(PlanningCacheManager.key_for_empire_list(user_id))
        PlanningCacheManager.delete_pattern(f'*{user_id}:empire:retrieve*')

        # details
        PlanningCacheManager.delete(PlanningCacheManager.key_for_empire_retrieve(user_id, empire_uuid))

    transaction.on_commit(clear_cache)


@receiver([post_save, post_delete], sender=PlanningEmpirePlan, dispatch_uid='planning_invalidate_empire_plan_caches')
def invalidate_empire_plan_caches(
    sender: type[PlanningEmpirePlan], instance: PlanningEmpirePlan, **kwargs: Any
) -> None:
    # get ids without additional db lookups
    user_id: int = instance.user_id  # type: ignore

    def clear_cache():
        PlanningCacheManager.delete_pattern(f'*PLANNING:{user_id}:*')

    transaction.on_commit(clear_cache)


@receiver([post_save, post_delete], sender=PlanningCX, dispatch_uid='planning_invalidate_cx_caches')
def invalidate_cx_caches(sender: type[PlanningCX], instance: PlanningCX, **kwargs: Any) -> None:
    user_id: int = instance.user_id  # type: ignore

    def clear_cache():
        # cxs are attached to plans and empires, so we need to do a full user cleaning
        PlanningCacheManager.delete_pattern(f'*PLANNING:{user_id}:*')

    transaction.on_commit(clear_cache)
