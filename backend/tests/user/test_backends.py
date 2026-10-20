from unittest.mock import patch

import bcrypt
import pytest
from django.contrib.auth import authenticate
from django.contrib.auth.hashers import make_password
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


LONG_PASSWORD = ('Xk7!qzR9pLm2' * 9)[:100]  # over bcrypt's 72-byte limit


def _login(api_client, username: str, password: str) -> int:
    with patch('user.api.urls.request_fio_refresh'):
        response = api_client.post(
            reverse('user:token_obtain_pair'), data={'username': username, 'password': password}, format='json'
        )
    return response.status_code


class TestLongPasswords:
    def test_unknown_user_gets_401(self, api_client) -> None:
        assert _login(api_client, 'nobody', LONG_PASSWORD) == 401

    def test_wrong_password_on_a_bcrypt_hash_gets_401(self, api_client) -> None:
        baker.make('user.User', username='pilot', password=make_password(PASSWORD, hasher='bcrypt'))

        assert _login(api_client, 'pilot', LONG_PASSWORD) == 401

    def test_bcrypt_hash_is_upgraded_to_bcrypt_sha256(self) -> None:
        user = baker.make('user.User', username='pilot', password=make_password(PASSWORD, hasher='bcrypt'))
        assert user.password.startswith('bcrypt$')

        assert authenticate(username='pilot', password=PASSWORD) == user

        user.refresh_from_db()
        assert user.password.startswith('bcrypt_sha256$')

    def test_legacy_hash_of_the_first_72_bytes_logs_in_with_the_full_password(self, api_client) -> None:
        legacy_hash = bcrypt.hashpw(LONG_PASSWORD.encode()[:72], bcrypt.gensalt(rounds=4)).decode()
        user = baker.make('user.User', username='veteran', password=legacy_hash)

        assert _login(api_client, 'veteran', LONG_PASSWORD) == 200

        user.refresh_from_db()
        assert user.password.startswith('bcrypt_sha256$')
        assert _login(api_client, 'veteran', LONG_PASSWORD) == 200
        # the new hash covers the whole password, not just its first 72 bytes
        assert _login(api_client, 'veteran', LONG_PASSWORD[:72]) == 401
