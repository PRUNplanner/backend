from collections.abc import Callable
from typing import Any

from core.services.cache_manager import CacheManager
from django.http import HttpResponse


class AnalyticsCacheManager(CacheManager):
    BASE_KEY = 'ANALYTICS'

    CACHE_TIMEOUT_3HOURS = 60 * 60 * 3

    # Keys
    @classmethod
    def key_for_plan_aggregate(cls, planet_natural_id: str) -> str:
        return cls.make_key('plan_aggregate', planet_natural_id)

    @classmethod
    def key_planning_insight_materials(cls) -> str:
        return cls.make_key('planning_insight_materials')

    # Operations
    @classmethod
    def get_plan_aggregate_response(cls, planet_natural_id: str, func: Callable[[], Any]) -> HttpResponse:
        key = cls.key_for_plan_aggregate(planet_natural_id)
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT_3HOURS)

    @classmethod
    def get_planning_insight_materials(cls, func: Callable[[], Any]) -> HttpResponse:
        key = cls.key_planning_insight_materials()
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT_3HOURS)
