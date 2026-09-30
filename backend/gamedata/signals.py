from typing import Any

from core.services.cache_manager import CacheManager
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from gamedata.gamedata_cache_manager import PLANET, STORAGE
from gamedata.models import GameFIOPlayerData, GamePlanet


@receiver([post_save, post_delete], sender=GamePlanet)
def invalidate_planet_cache(sender: type[GamePlanet], instance: GamePlanet, **kwargs: Any) -> None:
    update_fields = kwargs.get('update_fields')
    if update_fields and GamePlanet.AUTOMATION_FIELDS.issuperset(update_fields):
        return  # refresh bookkeeping only, nothing the planet endpoints serve

    CacheManager.invalidate_on_commit(PLANET, instance.planet_natural_id)


@receiver([post_save, post_delete], sender=GameFIOPlayerData)
def invalidate_user_storage_cache(sender: type[GameFIOPlayerData], instance: GameFIOPlayerData, **kwargs: Any) -> None:
    update_fields = kwargs.get('update_fields')
    if update_fields and GameFIOPlayerData.AUTOMATION_FIELDS.issuperset(update_fields):
        return  # refresh bookkeeping only, nothing the storage endpoint serves

    user_id: int = instance.user_id  # type: ignore
    CacheManager.invalidate_on_commit(STORAGE, user_id)
