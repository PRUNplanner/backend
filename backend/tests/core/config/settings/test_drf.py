import pytest
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
