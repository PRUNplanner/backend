import decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from core.services.cache_manager import CacheManager
from django.http import HttpResponse


class TestCacheManager:
    def test_make_key(self):
        uid = uuid4()
        key = CacheManager.make_key('user', 123, uid)
        assert key == f'BASE:user:123:{uid}'

    @patch('core.services.cache_manager.cache')
    def test_basic_operations(self, mock_cache):

        CacheManager.set('foo', 'bar', timeout=100)
        mock_cache.set.assert_called_with('foo', 'bar', 100)

        CacheManager.get('foo')
        mock_cache.get.assert_called_with('foo')

        CacheManager.delete('foo')
        mock_cache.delete.assert_called_with('foo')

        CacheManager.delete_pattern('user:*')
        mock_cache.delete_pattern.assert_called_with('user:*')

    @patch('core.services.cache_manager.cache')
    def test_get_response(self, mock_cache):

        mock_cache.get.return_value = None
        assert CacheManager.get_response('miss') is None

        mock_cache.get.return_value = {'data': b'{"json": "true"}'}
        response = CacheManager.get_response('hit', timeout=60)

        assert isinstance(response, HttpResponse)
        assert response.content == b'{"json": "true"}'
        assert response['X-Cache-Hit'] == '1'
        assert 'max-age=60' in response['Cache-Control']

    @pytest.mark.parametrize('scenario, fmt', [('miss', 'json'), ('hit', 'json'), ('miss', 'csv')])
    @patch('core.services.cache_manager.cache')
    def test_get_or_set_response(self, mock_cache, scenario, fmt):
        key = 'test_key'

        raw_data = {'price': decimal.Decimal('10.50'), 'id': uuid4()}

        if scenario == 'hit':
            mock_cache.get.return_value = b'{"cached": true}'
        else:
            mock_cache.get.return_value = None

        func = MagicMock(return_value=raw_data)

        response = CacheManager.get_or_set_response(key, func, fmt=fmt)

        if scenario == 'miss':
            func.assert_called_once()
            mock_cache.set.assert_called_once()
            assert response['X-Cache-Hit'] == '0'
        else:
            assert response['X-Cache-Hit'] == '1'

        if fmt == 'csv':
            assert response['Content-Type'] == 'text/csv; charset=utf-8'
        else:
            assert response['Content-Type'] == 'application/json'

    def test_build_response(self):
        data = {'status': 'ok'}
        response = CacheManager.build_response(data, timeout=500)
        assert response.data == data
        assert response['X-Cache-Hit'] == '0'
        assert 'max-age=500' in response['Cache-Control']
