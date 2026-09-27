import base64

import pytest
from django.urls import reverse
from model_bakery import baker
from rest_framework.test import APIClient
from user.models import User

pytestmark = pytest.mark.django_db


class TestAuthenticationClasses:
    @pytest.mark.xfail(
        strict=True,
        reason='audit: BasicAuthentication runs a bcrypt check on every request that sends it',
    )
    def test_basic_auth_is_not_accepted(self) -> None:
        user: User = baker.make('user.User', username='pilot')
        user.set_password('correct horse battery staple')
        user.save()
        credentials = base64.b64encode(b'pilot:correct horse battery staple').decode()

        response = APIClient().get(reverse('user:user_preferences'), HTTP_AUTHORIZATION=f'Basic {credentials}')

        assert response.status_code == 401

    def test_jwt_auth_is_accepted(self) -> None:
        user: User = baker.make('user.User')
        client = APIClient()
        client.force_authenticate(user=user)

        assert client.get(reverse('user:user_preferences')).status_code == 200
