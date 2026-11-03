from unittest.mock import patch

import bcrypt
import pytest
from django.contrib.auth import authenticate
from django.contrib.auth.hashers import make_password
from django.test import RequestFactory
from django.urls import reverse
from model_bakery import baker
from rest_framework.exceptions import Throttled
from rest_framework.request import Request
from rest_framework.test import APIClient
from structlog.testing import capture_logs
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


def _hasher_calls() -> tuple:
    # bcrypt_sha256 and bcrypt hash through bcrypt.hashpw (verify re-encodes), legacy $2b$ hashes through bcrypt.checkpw
    return patch('bcrypt.hashpw', wraps=bcrypt.hashpw), patch('bcrypt.checkpw', wraps=bcrypt.checkpw)


class TestOneHashPerAttempt:
    @pytest.mark.parametrize('username', ['pilot', 'nobody', 'veteran'])
    def test_failed_login_costs_exactly_one_hash(self, username: str) -> None:
        baker.make('user.User', username='pilot', password=make_password(PASSWORD))
        _legacy_user()
        hashpw, checkpw = _hasher_calls()

        with hashpw as hashed, checkpw as checked:
            assert authenticate(username=username, password='wrong-password') is None

        assert hashed.call_count + checked.call_count == 1


class TestFailedLoginCounter:
    def _fail(self, api_client, n: int, username: str = 'pilot', ip: str | None = None) -> list[int]:
        statuses = []
        for i in range(n):
            with patch('user.api.urls.request_fio_refresh'):
                response = api_client.post(
                    reverse('user:token_obtain_pair'),
                    data={'username': username, 'password': 'wrong-password'},
                    format='json',
                    REMOTE_ADDR=ip or f'10.0.0.{i}',
                )
            statuses.append(response.status_code)
        return statuses

    def test_eleventh_failure_for_a_username_is_throttled_across_idents(self, api_client, locmem_cache) -> None:
        baker.make('user.User', username='pilot', password=make_password(PASSWORD))
        assert self._fail(api_client, 10) == [401] * 10

        hashpw, checkpw = _hasher_calls()
        with hashpw as hashed, checkpw as checked, capture_logs() as logs:
            response = api_client.post(
                reverse('user:token_obtain_pair'),
                data={'username': 'PILOT', 'password': PASSWORD},
                format='json',
                REMOTE_ADDR='10.0.1.1',
            )
            self._fail(api_client, 2, ip='10.0.1.2')

        assert response.status_code == 429
        assert int(response['Retry-After']) > 0
        assert hashed.call_count + checked.call_count == 0
        assert [line['by'] for line in logs if line['event'] == 'login_throttled'] == ['username']

        locmem_cache.clear()  # the window has passed
        assert _login(api_client, 'pilot', PASSWORD) == 200

    def test_eleventh_failure_from_one_ident_is_throttled_across_usernames(self, locmem_cache) -> None:
        # straight to authenticate(): on the JWT view auth_login (10/h per IP) would answer 429 first
        def attempt(username: str, ip: str) -> None:
            request = Request(RequestFactory().post('/', REMOTE_ADDR=ip))
            # a DRF Request, as simplejwt passes it
            user = authenticate(request, username=username, password='wrong-password')  # ty:ignore[invalid-argument-type]
            assert user is None

        for i in range(10):
            attempt(f'user{i}', '10.9.9.9')

        with pytest.raises(Throttled):
            attempt('someone-else', '10.9.9.9')
        attempt('someone-else', '10.9.9.8')

    def test_admin_login_shares_the_counter(self, client, locmem_cache) -> None:
        baker.make('user.User', username='pilot', password=make_password(PASSWORD), is_staff=True, is_superuser=True)
        self._fail(APIClient(), 10)

        response = client.post(reverse('admin:login'), {'username': 'pilot', 'password': PASSWORD, 'next': '/'})

        # the form is shown again, no session
        assert response.status_code == 200
        assert '_auth_user_id' not in client.session

    def test_admin_failures_count_too(self, client, locmem_cache) -> None:
        baker.make('user.User', username='pilot', password=make_password(PASSWORD), is_staff=True, is_superuser=True)
        for i in range(10):
            client.post(reverse('admin:login'), {'username': 'pilot', 'password': 'wrong'}, REMOTE_ADDR=f'10.0.0.{i}')

        assert _login(APIClient(), 'pilot', PASSWORD) == 429
