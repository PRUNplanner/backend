from core.services.cache_manager import CacheManager
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from user.models import User, UserPreference

from planning.models import PlanningCX, PlanningEmpire, PlanningEmpirePlan, PlanningPlan
from planning.planning_cache_manager import PLANNING


# plans, empires and cxs are all nested in each other's payloads, so any change drops all of the user's planning caches
@receiver([post_save, post_delete], sender=PlanningPlan, dispatch_uid='planning_invalidate_plan_caches')
@receiver([post_save, post_delete], sender=PlanningEmpire, dispatch_uid='planning_invalidate_empire_caches')
@receiver([post_save, post_delete], sender=PlanningEmpirePlan, dispatch_uid='planning_invalidate_empire_plan_caches')
@receiver([post_save, post_delete], sender=PlanningCX, dispatch_uid='planning_invalidate_cx_caches')
def invalidate_user_planning_caches(
    sender: type[PlanningPlan | PlanningEmpire | PlanningEmpirePlan | PlanningCX],
    instance: PlanningPlan | PlanningEmpire | PlanningEmpirePlan | PlanningCX,
    **kwargs: object,
) -> None:
    # get ids without additional db lookups
    user_id: int = instance.user_id  # type: ignore

    CacheManager.invalidate_on_commit(PLANNING, user_id)


@receiver(post_delete, sender=PlanningPlan, dispatch_uid='planning_drop_plan_overrides')
def drop_plan_overrides(
    sender: type[PlanningPlan], instance: PlanningPlan, origin: object = None, **kwargs: object
) -> None:
    # account deletion removes the preferences anyway
    if isinstance(origin, User):
        return

    # replaces the frontend's cleanPlanPreferences(), which also dropped plans created in another tab
    preference = UserPreference.objects.select_for_update().filter(user_id=instance.user_id).first()  # type: ignore
    overrides = preference.preferences.get('plan_overrides') if preference else None
    if preference and overrides and str(instance.uuid) in overrides:
        del overrides[str(instance.uuid)]
        preference.save(update_fields=['preferences'])
