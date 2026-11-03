import importlib
from collections.abc import Iterator

import api.urls
import core.urls
import pytest
from django.test import Client, override_settings
from django.urls import clear_url_caches


def _reload_urls() -> None:
    # urls.py reads DEBUG at import; core.urls holds a resolver built from api.urls, so reload both
    importlib.reload(api.urls)
    importlib.reload(core.urls)
    clear_url_caches()


@pytest.fixture
def restore_urls() -> Iterator[None]:
    yield
    _reload_urls()


@pytest.mark.django_db
@pytest.mark.parametrize('debug, status', [(False, 404), (True, 200)])
def test_api_auth_only_with_debug(client: Client, restore_urls: None, debug: bool, status: int) -> None:
    with override_settings(DEBUG=debug):
        _reload_urls()
    assert client.get('/api-auth/login/').status_code == status
