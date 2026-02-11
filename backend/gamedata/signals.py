from typing import Any, cast

from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django_redis.cache import RedisCache

from gamedata.gamedata_cache_manager import GamedataCacheManager
from gamedata.models import GameFIOPlayerData, GamePlanet

redis_cache = cast(RedisCache, cache)


@receiver([post_save, post_delete], sender=GamePlanet)
def invalidate_planet_cache(sender: type[GamePlanet], instance: GamePlanet, **kwargs: Any) -> None:
    def clear_cache():
        GamedataCacheManager.delete(GamedataCacheManager.key_planet_get(instance.planet_natural_id))

    transaction.on_commit(clear_cache)


@receiver([post_save, post_delete], sender=GameFIOPlayerData)
def invalidate_user_storage_cache(sender: type[GameFIOPlayerData], instance: GameFIOPlayerData, **kwargs: Any) -> None:
    user_id: int = instance.user_id  # type: ignore

    def clear_cache():
        GamedataCacheManager.delete(GamedataCacheManager.key_user_storage(user_id))

    transaction.on_commit(clear_cache)
