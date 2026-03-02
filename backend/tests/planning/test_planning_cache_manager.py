from unittest.mock import patch
from uuid import uuid4

import pytest
from planning.planning_cache_manager import PlanningCacheManager


class TestPlanningCacheManager:
    @pytest.mark.parametrize(
        'method_name, args, expected_parts',
        [
            ('key_for_plan_list', [1], ['1', 'plan', 'list']),
            ('key_plan_retrieve', [1, uuid4()], ['1', 'plan', 'retrieve']),
            ('key_for_empire_list', [1], ['1', 'empire', 'list']),
            ('key_for_empire_retrieve', [1, uuid4()], ['1', 'empire', 'retrieve']),
            ('key_for_empire_retrieve_plans', [1, uuid4()], ['1', 'empire', 'retrieve', 'plans']),
            ('key_for_cx_list', [1], ['1', 'cx', 'list']),
            ('key_for_cx_retrieve', [1, uuid4()], ['1', 'cx', 'retrieve']),
        ],
    )
    def test_key_generation(self, method_name, args, expected_parts):
        method = getattr(PlanningCacheManager, method_name)
        key = method(*args)

        assert key.startswith('PLANNING:')
        for part in expected_parts:
            assert part in key

        if isinstance(args[-1], uuid4().__class__):
            assert str(args[-1]) in key

    @pytest.mark.parametrize(
        'method_name, extra_args',
        [
            ('get_plan_list_response', []),
            ('get_plan_retrieve_response', [uuid4()]),
            ('get_empire_list_response', []),
            ('get_empire_retrieve_response', [uuid4()]),
            ('get_empire_retrieve_plans_response', [uuid4()]),
            ('get_cx_list_response', []),
            ('get_cx_retrieve_response', [uuid4()]),
        ],
    )
    @patch('core.services.cache_manager.CacheManager.get_or_set_response')
    def test_response_methods(self, mock_get_or_set, method_name, extra_args):
        user_id = 123

        def sample_data_func():
            return {'test': 'data'}

        # Build argument list: user_id, optional_id, then the callable
        call_args = [user_id] + extra_args + [sample_data_func]

        method = getattr(PlanningCacheManager, method_name)
        method(*call_args)

        assert mock_get_or_set.called
        assert mock_get_or_set.call_args.kwargs['timeout'] == 3600
