from collections.abc import Callable, Mapping, Sequence

import pytest
from core.throttling import AuthenticatedUserRateThrottle
from django.core.cache.backends.locmem import LocMemCache
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken
from structlog.testing import capture_logs
from user.models import User, UserAPIKey

pytestmark = pytest.mark.django_db

PROFILE = reverse('user:user_profile')


def rate_limited(logs: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return [line for line in logs if line['event'] == 'rate_limited']


class TestAuthenticatedUserRateThrottle:
    def test_request_241_of_a_user_is_throttled(
        self, api_client: APIClient, user_factory: Callable[..., User], locmem_cache: LocMemCache
    ) -> None:
        client = api_client.as_user(user_factory())  # ty:ignore[unresolved-attribute]
        for _ in range(240):
            assert client.get(PROFILE).status_code == 200

        response = client.get(PROFILE)

        assert response.status_code == 429
        assert int(response['Retry-After']) > 0
        other = APIClient()
        other.force_authenticate(user_factory())
        assert other.get(PROFILE).status_code == 200

    def test_jwt_and_api_key_share_the_count(
        self,
        api_client: APIClient,
        user_factory: Callable[..., User],
        locmem_cache: LocMemCache,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(AuthenticatedUserRateThrottle, 'THROTTLE_RATES', {'user': '4/min'})
        user = user_factory()
        _, key = UserAPIKey.objects.create_key(name='script', user=user)
        jwt = {'HTTP_AUTHORIZATION': f'Bearer {AccessToken.for_user(user)}'}
        api_key = {'HTTP_AUTHORIZATION': f'Api-Key {key}'}

        for headers in (jwt, jwt, api_key, api_key):
            assert api_client.get(PROFILE, **headers).status_code == 200

        assert api_client.get(PROFILE, **jwt).status_code == 429
        assert api_client.get(PROFILE, **api_key).status_code == 429

    def test_anonymous_requests_are_not_counted(self, api_client: APIClient, locmem_cache: LocMemCache) -> None:
        # a public route without a throttle scope (the per-ticker CXPC routes have a per-IP one)
        url = reverse('data:material-list')

        statuses = {api_client.get(url).status_code for _ in range(300)}

        assert 429 not in statuses

    def test_logged_once_per_window(
        self,
        api_client: APIClient,
        user_factory: Callable[..., User],
        locmem_cache: LocMemCache,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(AuthenticatedUserRateThrottle, 'THROTTLE_RATES', {'user': '2/min'})
        user = user_factory()
        client = api_client.as_user(user)  # ty:ignore[unresolved-attribute]

        with capture_logs() as logs:
            statuses = [client.get(PROFILE).status_code for _ in range(6)]

        assert statuses == [200, 200, 429, 429, 429, 429]
        (line,) = rate_limited(logs)
        assert line == {
            'event': 'rate_limited',
            'log_level': 'warning',
            'scope': 'user',
            'user_id': user.pk,
            # force_authenticate skips the authentication classes that mark it
            'auth': 'none',
            'path_template': '/user/profile/',
            'rate': '2/min',
        }

    def test_retry_after_is_readable_cross_origin(
        self,
        api_client: APIClient,
        user_factory: Callable[..., User],
        locmem_cache: LocMemCache,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(AuthenticatedUserRateThrottle, 'THROTTLE_RATES', {'user': '1/min'})
        client = api_client.as_user(user_factory())  # ty:ignore[unresolved-attribute]
        client.get(PROFILE)

        response = client.get(PROFILE, HTTP_ORIGIN='https://prunplanner.org')

        assert response.status_code == 429
        assert 'Retry-After' in response['Access-Control-Expose-Headers']


class TestLoggedScopedRateThrottle:
    def test_scoped_limit_unchanged_and_logged(self, api_client: APIClient, locmem_cache: LocMemCache) -> None:
        url = reverse('client-errors')
        body = {'kind': 'network', 'method': 'GET', 'path_template': '/data/storage/', 'release': '1.2.3'}
        for _ in range(30):
            assert api_client.post(url, body, format='json').status_code == 204

        with capture_logs() as logs:
            assert api_client.post(url, body, format='json').status_code == 429
            assert api_client.post(url, body, format='json').status_code == 429

        (line,) = rate_limited(logs)
        assert line['scope'] == 'client_error'
        assert line['user_id'] is None
