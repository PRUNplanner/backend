from collections.abc import Iterator
from typing import cast

import orjson
import pytest
from django.core.cache import caches
from django.core.cache.backends.locmem import LocMemCache
from django.test import Client
from model_bakery import baker
from rest_framework.test import APIClient


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
def superuser():
    # no email: pytest-django's admin_user has one, and a new email queues a real verification task
    return baker.make('user.User', is_superuser=True, is_staff=True, is_active=True)


@pytest.fixture
def admin_client(superuser) -> Client:
    """A client logged in to the admin as `superuser` (replaces pytest-django's fixture of the same name)."""
    admin = Client()
    admin.force_login(superuser)
    return admin


@pytest.fixture
def user_factory(**kwargs):
    # admin: is_superuser = True
    # staff: is_staff = True
    return lambda **kwargs: baker.make('user.User', **kwargs)


@pytest.fixture
def locmem_cache(settings) -> Iterator[LocMemCache]:
    """
    Swaps the DummyCache of the test settings for a real
    in-memory cache. Required by every test asserting cache hits or invalidation.
    """
    settings.CACHES = {
        'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'tests-locmem'},
    }
    backend = cast(LocMemCache, caches['default'])
    backend.clear()

    yield backend

    backend.clear()


@pytest.fixture(scope='session', autouse=True)
def create_unmanaged_tables(django_db_setup, django_db_blocker):
    # the exchange analytics materialized view is unmanaged; SQLite gets a plain table in its place
    with django_db_blocker.unblock():
        from django.db import connection

        with connection.cursor() as cursor:
            # Manually create the table schema here
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prunplanner_game_exchanges_analytics (
                    id integer PRIMARY KEY AUTOINCREMENT,
                    ticker varchar(20),
                    exchange_code varchar(20),
                    date_epoch bigint,
                    calendar_date date,
                    traded_daily integer,
                    vwap_daily decimal,
                    sum_traded_7d integer,
                    avg_traded_7d decimal,
                    vwap_7d decimal,
                    sum_traded_30d integer,
                    avg_traded_30d decimal,
                    vwap_30d decimal
                )
            """)
