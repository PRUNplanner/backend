from core.services.cache_manager import CacheNamespace
from django.core.cache import cache

HOUR = 60 * 60
DAY = 24 * HOUR

# edge_max_age: how long Cloudflare keeps a public response (s-maxage), following the frontend's own hold
# (frontend/src/lib/config.ts, gameData.queries.ts); the edge age and the app's hold add up

# app holds 24 h; after an admin import, purge these three URLs by hand
MATERIALS = CacheNamespace('gamedata:materials', DAY, edge_max_age=DAY)
RECIPES = CacheNamespace('gamedata:recipes', DAY, edge_max_age=DAY)
BUILDINGS = CacheNamespace('gamedata:buildings', DAY, edge_max_age=DAY)
# invalidated by the FIO exchange webhook about once a minute, a long ttl only piles up orphaned versions.
# app holds 30 min (15 min market report); edge kept short as people trade on these prices
EXCHANGES = CacheNamespace('gamedata:exchanges', 10 * 60, edge_max_age=5 * 60)
# app holds 15 min (market summary 30 min)
CXPC = CacheNamespace('gamedata:cxpc', 3 * HOUR, edge_max_age=15 * 60)
# scope: planet natural id. 1 h, as the active cogc program is computed against the build time. app holds 3 h
PLANET = CacheNamespace('gamedata:planet:v3', HOUR, edge_max_age=3 * HOUR)
# single planet refreshes (~9 s apart) rely on the short ttl, full imports invalidate. app holds 3 h
PLANET_LIST = CacheNamespace('gamedata:planet-list:v2', 15 * 60, edge_max_age=3 * HOUR)
# same ttl as the planet list; full imports invalidate, single refreshes rely on the ttl. app holds 3 h
PLANET_SEARCH_INDEX = CacheNamespace('gamedata:planet-search-index:v3', 15 * 60, edge_max_age=3 * HOUR)
# bump the version when the response shape changes, so cached old shapes aren't served until the ttl ends
STORAGE = CacheNamespace('gamedata:storage:v2', 3 * HOUR, private=True)


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
