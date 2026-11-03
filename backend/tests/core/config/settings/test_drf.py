import pytest
from core.config.django import local
from core.config.settings import drf
from django.urls import reverse
from model_bakery import baker
from rest_framework.test import APIClient
from user.models import User

pytestmark = pytest.mark.django_db


class TestAuthenticationClasses:
    def test_jwt_auth_is_accepted(self) -> None:
        user: User = baker.make('user.User')
        client = APIClient()
        client.force_authenticate(user=user)

        assert client.get(reverse('user:user_preferences')).status_code == 200


BROWSABLE = 'rest_framework.renderers.BrowsableAPIRenderer'


def test_production_renders_json_only() -> None:
    assert drf.REST_FRAMEWORK['DEFAULT_RENDERER_CLASSES'] == ['api.renderers.OrjsonRenderer']


def test_local_adds_the_browsable_api() -> None:
    assert local.REST_FRAMEWORK['DEFAULT_RENDERER_CLASSES'] == ['api.renderers.OrjsonRenderer', BROWSABLE]
    # the star import shares the dict with base: local must not change it in place
    assert drf.REST_FRAMEWORK['DEFAULT_RENDERER_CLASSES'] == ['api.renderers.OrjsonRenderer']
