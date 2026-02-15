from collections.abc import Callable
from typing import Any

from core.services.cache_manager import CacheManager
from django.http import HttpResponse
from rest_framework.response import Response


class GamedataCacheManager(CacheManager):
    BASE_KEY = 'GAMEDATA'

    CACHE_TIMEOUT = 60 * 15
    CACHE_TIMEOUT_30MIN = 60 * 30
    CACHE_TIMEOUT_3HOURS = 60 * 60 * 3
    CACHE_TIMEOUT_1DAY = 60 * 60 * 24

    # Keys
    @classmethod
    def key_material_list(cls) -> str:
        return cls.make_key('material', 'list')

    @classmethod
    def key_recipe_list(cls) -> str:
        return cls.make_key('recipe', 'list')

    @classmethod
    def key_building_list(cls) -> str:
        return cls.make_key('building', 'list')

    @classmethod
    def key_exchange_list(cls) -> str:
        return cls.make_key('exchange', 'list')

    @classmethod
    def key_planet_list(cls) -> str:
        return cls.make_key('planet', 'list')

    @classmethod
    def key_planet_get(cls, planet_natural_id: str) -> str:
        return cls.make_key('planet', planet_natural_id)

    @classmethod
    def key_planet_multiple(cls, planet_natural_ids: list[str]) -> str:
        return cls.make_key('planet', *planet_natural_ids)

    @classmethod
    def key_planet_popr(cls, planet_natural_id: str) -> str:
        return cls.make_key('planet', 'popr', planet_natural_id)

    @classmethod
    def key_user_storage(cls, user_id: int) -> str:
        return cls.make_key('storage', user_id)

    @classmethod
    def key_exchange_cxpc_response(cls, ticker: str, exchange_code: str | None) -> str:
        if exchange_code:
            return cls.make_key('exchange', 'cxpc', ticker, exchange_code)
        else:
            return cls.make_key('exchange', 'cxpc', ticker)

    @classmethod
    def key_planet_search(cls, search_request: dict[str, list[str] | bool]) -> str:
        parts = []
        for key in sorted(search_request.keys()):
            value = search_request[key]

            if isinstance(value, list):
                value_str = ','.join(sorted(value))
            elif isinstance(value, bool):
                value_str = 'TRUE' if value else 'FALSE'
            else:
                value_str = str(value)
            parts.append(value_str)

        return cls.make_key('planet', 'search', *parts)

    # Operations
    @classmethod
    def get_material_list_response(cls, func: Callable[[], Any]) -> Response | HttpResponse:
        key = cls.key_material_list()
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT_1DAY)

    @classmethod
    def get_recipe_list_response(cls, func: Callable[[], Any]) -> Response | HttpResponse:
        key = cls.key_recipe_list()
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT_1DAY)

    @classmethod
    def get_building_list_response(cls, func: Callable[[], Any]) -> Response | HttpResponse:
        key = cls.key_building_list()
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT_1DAY)

    @classmethod
    def get_exchange_list_response(cls, func: Callable[[], Any]) -> Response | HttpResponse:
        key = cls.key_exchange_list()
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT)

    @classmethod
    def get_planet_list_response(cls, func: Callable[[], Any]) -> Response | HttpResponse:
        key = cls.key_planet_list()
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT_1DAY)

    @classmethod
    def get_planet_get_response(cls, planet_natural_id: str, func: Callable[[], Any]) -> Response | HttpResponse:
        key = cls.key_planet_get(planet_natural_id)
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT_1DAY)

    @classmethod
    def get_planet_multiple_response(
        cls, planet_natural_ids: list[str], func: Callable[[], Any]
    ) -> Response | HttpResponse:
        key = cls.key_planet_multiple(planet_natural_ids)
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT_30MIN)

    @classmethod
    def get_storage_response(cls, user_id: int, func: Callable[[], Any]) -> Response | HttpResponse:
        key = cls.key_user_storage(user_id)
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT_3HOURS)

    @classmethod
    def get_planet_search_response(
        cls, search_request: dict[str, list[str] | bool], func: Callable[[], Any]
    ) -> Response | HttpResponse:
        key = cls.key_planet_search(search_request)
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT_30MIN)

    @classmethod
    def get_exchange_cxpc_response(
        cls,
        ticker: str,
        exchange_code: str | None,
        func: Callable[[], Any],
    ) -> Response | HttpResponse:
        key = cls.key_exchange_cxpc_response(ticker, exchange_code)
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT_3HOURS)

    @classmethod
    def get_planet_latest_popr(cls, planet_natural_id: str, func: Callable[[], Any]) -> Response | HttpResponse:
        key = cls.key_planet_popr(planet_natural_id)
        return cls.get_or_set_response(key, func, timeout=cls.CACHE_TIMEOUT_1DAY)
