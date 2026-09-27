from collections.abc import Iterator
from typing import cast

import orjson
import pytest
from django.core.cache import caches
from django.test import Client
from model_bakery import baker
from rest_framework.test import APIClient
from tests.cache_backends import PatternLocMemCache


@pytest.fixture
def api_client():
    """
    Patches DRF APIClient to allow json.loads in ALL responses and
    forcing user authentication with api_client.as_user(user).method(url)
    """

    client = APIClient()
    methods_to_patch = ['get', 'post', 'put', 'patch', 'delete']

    def create_patch(original_method):
        def patched_method(*args, **kwargs):
            response = original_method(*args, **kwargs)

            if not hasattr(response, 'data'):
                try:
                    response.data = orjson.loads(response.content)
                except (ValueError, TypeError, orjson.JSONDecodeError):
                    response.data = None
            return response

        return patched_method

    for method_name in methods_to_patch:
        original = getattr(client, method_name)
        setattr(client, method_name, create_patch(original))

    def as_user(user):
        client.force_authenticate(user=user)
        return client

    client.as_user = as_user  # type: ignore
    return client


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def user_factory(**kwargs):
    # admin: is_superuser = True
    # staff: is_staff = True
    return lambda **kwargs: baker.make('user.User', **kwargs)


@pytest.fixture
def locmem_cache(settings) -> Iterator[PatternLocMemCache]:
    """
    Swaps the DummyCache of the test settings for a real, pattern-capable
    in-memory cache. Required by every test asserting cache hits or invalidation.
    """
    settings.CACHES = {
        'default': {'BACKEND': 'tests.cache_backends.PatternLocMemCache', 'LOCATION': 'tests-locmem'},
    }
    backend = cast(PatternLocMemCache, caches['default'])
    backend.clear()
    PatternLocMemCache.pattern_calls.clear()

    yield backend

    backend.clear()
    PatternLocMemCache.pattern_calls.clear()
