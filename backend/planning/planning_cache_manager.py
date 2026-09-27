from core.services.cache_manager import CacheNamespace

# plans, empires and cxs are nested in each other's payloads, so one per-user namespace covers them all
PLANNING = CacheNamespace('planning', 60 * 60, private=True)
