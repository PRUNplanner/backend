from typing import Any
from unittest.mock import patch

import pytest
from gamedata.gamedata_cache_manager import GamedataCacheManager


class TestGamedataCacheManager:
    @pytest.mark.parametrize(
        'method_name, args, expected_parts',
        [
            ('key_material_list', [], ['material', 'list']),
            ('key_recipe_list', [], ['recipe', 'list']),
            ('key_building_list', [], ['building', 'list']),
            ('key_exchange_list', ['csv'], ['exchange', 'list', 'csv']),
            ('key_planet_list', [], ['planet', 'list']),
            ('key_planet_get', ['MORIA'], ['planet', 'MORIA']),
            ('key_planet_searchterm', ['term'], ['planet', 'search_term', 'term']),
            ('key_planet_multiple', [['A', 'B']], ['planet', 'A', 'B']),
            ('key_planet_popr', ['MORIA'], ['planet', 'popr', 'MORIA']),
            ('key_user_storage', [123], ['storage', '123']),
        ],
    )
    def test_basic_keys(self, method_name, args, expected_parts):
        method = getattr(GamedataCacheManager, method_name)
        key = method(*args)
        assert key.startswith('GAMEDATA:')
        for part in expected_parts:
            assert part in key

    @pytest.mark.parametrize(
        'ticker, code, expected',
        [
            ('FE', 'AI1', 'GAMEDATA:exchange:cxpc:FE:AI1'),
            ('FE', None, 'GAMEDATA:exchange:cxpc:FE'),
        ],
    )
    def test_key_exchange_cxpc(self, ticker, code, expected):
        assert GamedataCacheManager.key_exchange_cxpc_response(ticker, code) == expected

    def test_key_planet_search_complex(self):
        search_req: dict[str, list[str] | bool] = {
            'materials': ['iron', 'copper'],
            'is_rocky': True,
            'is_gas': False,
        }
        key = GamedataCacheManager.key_planet_search(search_req)

        assert 'FALSE' in key
        assert 'TRUE' in key
        assert 'copper,iron' in key

    @pytest.mark.parametrize(
        'method_name, extra_args, timeout',
        [
            ('get_material_list_response', [], 86400),
            ('get_recipe_list_response', [], 86400),
            ('get_building_list_response', [], 86400),
            ('get_exchange_list_response', [], 86400),
            ('get_planet_list_response', [], 86400),
            ('get_planet_get_response', ['M1'], 86400),
            ('get_planet_multiple_response', [['M1']], 1800),
            ('get_storage_response', [1], 10800),
            ('get_planet_search_response', [{}], 1800),
            ('get_planet_searchterm', ['Complex-Term!'], 1800),
            ('get_exchange_cxpc_response', ['F', 'A'], 10800),
            ('get_planet_latest_popr', ['M1'], 86400),
        ],
    )
    @patch('core.services.cache_manager.CacheManager.get_or_set_response')
    def test_response_methods(self, mock_get_or_set, method_name, extra_args, timeout):
        def sample_func():
            return {'data': 'ok'}

        method = getattr(GamedataCacheManager, method_name)
        method(*extra_args, sample_func)

        assert mock_get_or_set.called
        assert mock_get_or_set.call_args.kwargs['timeout'] == timeout

    def test_planet_searchterm_sanitization(self):
        def sample_func():
            return {}

        with patch('core.services.cache_manager.CacheManager.get_or_set_response') as mock_set:
            GamedataCacheManager.get_planet_searchterm('  Hello-World!  ', sample_func)
            expected_key = GamedataCacheManager.key_planet_searchterm('hello_world_')
            assert mock_set.call_args.args[0] == expected_key

    def test_key_planet_search_branches(self):

        search_req: dict[str, Any] = {
            'materials': ['iron', 'copper'],
            'is_rocky': True,
            'tier': 'T1',  # This hits the 'else' branch
            'active': False,
        }

        key = GamedataCacheManager.key_planet_search(search_req)

        assert 'copper,iron' in key
        assert 'TRUE' in key
        assert 'FALSE' in key
        assert 'T1' in key

        assert key.startswith('GAMEDATA:planet:search')
