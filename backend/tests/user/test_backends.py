from unittest.mock import patch

import bcrypt
import pytest
from django.contrib.auth import authenticate
from django.urls import reverse
from model_bakery import baker
from user.models import User

pytestmark = pytest.mark.django_db

PASSWORD = 'legacy-pass-1'


def _legacy_user(is_active: bool = True) -> User:
    # accounts migrated from the old app keep their bcrypt hash until the first login
    legacy_hash = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    return baker.make('user.User', username='veteran', password=legacy_hash, is_active=is_active)


class TestLegacyAwareBackend:
    def test_legacy_hash_logs_in_and_is_upgraded(self) -> None:
        user = _legacy_user()

        assert authenticate(username='veteran', password=PASSWORD) == user

        user.refresh_from_db()
        assert not user.password.startswith('$2b$')
        assert user.check_password(PASSWORD)

    def test_wrong_password_keeps_the_legacy_hash(self) -> None:
        user = _legacy_user()
        legacy_hash = user.password

        assert authenticate(username='veteran', password='wrong') is None

        user.refresh_from_db()
        assert user.password == legacy_hash

    def test_deactivated_account_is_refused_and_not_upgraded(self) -> None:
        user = _legacy_user(is_active=False)
        legacy_hash = user.password

        assert authenticate(username='veteran', password=PASSWORD) is None

        user.refresh_from_db()
        assert user.password == legacy_hash

    @pytest.mark.parametrize(('username', 'password'), [('nobody', PASSWORD), ('veteran', None), (None, PASSWORD)])
    def test_unknown_user_or_missing_credentials(self, username: str | None, password: str | None) -> None:
        _legacy_user()

        assert authenticate(username=username, password=password) is None

    def test_login_endpoint_accepts_a_legacy_hash(self, api_client) -> None:
        _legacy_user()

        with patch('user.api.urls.request_fio_refresh'):
            response = api_client.post(
                reverse('user:token_obtain_pair'), data={'username': 'veteran', 'password': PASSWORD}, format='json'
            )

        assert response.status_code == 200
        assert 'access' in response.data
