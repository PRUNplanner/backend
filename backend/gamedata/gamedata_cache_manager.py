from core.services.cache_manager import CacheNamespace
from django.core.cache import cache

HOUR = 60 * 60
DAY = 24 * HOUR

MATERIALS = CacheNamespace('gamedata:materials', DAY)
RECIPES = CacheNamespace('gamedata:recipes', DAY)
BUILDINGS = CacheNamespace('gamedata:buildings', DAY)
EXCHANGES = CacheNamespace('gamedata:exchanges', DAY)
CXPC = CacheNamespace('gamedata:cxpc', 3 * HOUR)
# scope: planet natural id. 1 h, as the active cogc program is computed against the build time
PLANET = CacheNamespace('gamedata:planet', HOUR)
# single planet refreshes (~9 s apart) rely on the short ttl, full imports invalidate
PLANET_LIST = CacheNamespace('gamedata:planet-list', 15 * 60)
# same ttl as the planet list; full imports invalidate, single refreshes rely on the ttl
PLANET_SEARCH_INDEX = CacheNamespace('gamedata:planet-search-index', 15 * 60)
STORAGE = CacheNamespace('gamedata:storage', 3 * HOUR, private=True)


class GamedataCacheManager:
    """FIO refresh lock per user. A lock, not a response cache."""

    @staticmethod
    def key_user_fio_lock(user_id: int) -> str:
        return f'GAMEDATA:task:fio_refresh_lock:{user_id}'

    @classmethod
    def has_fio_refresh_lock(cls, user_id: int) -> bool:
        return cache.get(cls.key_user_fio_lock(user_id)) is not None

    @classmethod
    def set_fio_refresh_lock(cls, user_id: int) -> bool:
        return cache.add(cls.key_user_fio_lock(user_id), 'fio_storage_refresh_locked', 60 * 5)

    @classmethod
    def delete_fio_refresh_lock(cls, user_id: int) -> None:
        cache.delete(cls.key_user_fio_lock(user_id))
