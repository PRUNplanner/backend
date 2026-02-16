from collections.abc import Callable
from typing import Any
from uuid import UUID

from core.services.cache_manager import CacheManager


class PlanningCacheManager(CacheManager):
    BASE_KEY = 'PLANNING'

    CACHE_TIMEOUT_1Hour = 60 * 60

    # Keys
    ## Plan
    @classmethod
    def key_for_plan_list(cls, user_id: int) -> str:
        return cls.make_key(user_id, 'plan', 'list')

    @classmethod
    def key_plan_retrieve(cls, user_id: int, plan_id: UUID) -> str:
        return cls.make_key(user_id, 'plan', 'retrieve', plan_id)

    ## Empire
    @classmethod
    def key_for_empire_list(cls, user_id: int) -> str:
        return cls.make_key(user_id, 'empire', 'list')

    @classmethod
    def key_for_empire_retrieve(cls, user_id: int, empire_id: UUID) -> str:
        return cls.make_key(user_id, 'empire', 'retrieve', empire_id)

    @classmethod
    def key_for_empire_retrieve_plans(cls, user_id: int, empire_id: UUID) -> str:
        return cls.make_key(user_id, 'empire', 'retrieve', 'plans', empire_id)

    ## CX
    @classmethod
    def key_for_cx_list(cls, user_id: int) -> str:
        return cls.make_key(user_id, 'cx', 'list')

    @classmethod
    def key_for_cx_retrieve(cls, user_id: int, cx_id: UUID) -> str:
        return cls.make_key(user_id, 'cx', 'retrieve', cx_id)

    # Operations
    ## Plan
    @classmethod
    def get_plan_list_response(cls, user_id: int, func: Callable[[], Any]):
        key = cls.key_for_plan_list(user_id)
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT_1Hour)

    @classmethod
    def get_plan_retrieve_response(cls, user_id: int, plan_id: UUID, func: Callable[[], Any]):
        key = cls.key_plan_retrieve(user_id, plan_id)
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT_1Hour)

    ## Empire
    @classmethod
    def get_empire_list_response(cls, user_id: int, func: Callable[[], Any]):
        key = cls.key_for_empire_list(user_id)
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT_1Hour)

    @classmethod
    def get_empire_retrieve_response(cls, user_id: int, empire_id: UUID, func: Callable[[], Any]):
        key = cls.key_for_empire_retrieve(user_id, empire_id)
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT_1Hour)

    @classmethod
    def get_empire_retrieve_plans_response(cls, user_id: int, empire_id: UUID, func: Callable[[], Any]):
        key = cls.key_for_empire_retrieve_plans(user_id, empire_id)
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT_1Hour)

    ## CX
    @classmethod
    def get_cx_list_response(cls, user_id: int, func: Callable[[], Any]):
        key = cls.key_for_cx_list(user_id)
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT_1Hour)

    @classmethod
    def get_cx_retrieve_response(cls, user_id: int, cx_id: UUID, func: Callable[[], Any]):
        key = cls.key_for_cx_retrieve(user_id, cx_id)
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT_1Hour)
