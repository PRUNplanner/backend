from collections.abc import Callable, Mapping, Sequence
from unittest.mock import patch

import pytest
from core.throttling import AuthenticatedUserRateThrottle
from django.core.cache.backends.locmem import LocMemCache
from django.test import RequestFactory
from django.urls import reverse
from model_bakery import baker
from rest_framework.test import APIClient
from rest_framework.throttling import BaseThrottle, SimpleRateThrottle
from rest_framework_simplejwt.tokens import AccessToken
from structlog.testing import capture_logs
from user.models import User, UserAPIKey
from user.models.configs import GlobalConfigWebhook, WebhookSenderChoices

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

    def test_webhook_takes_20_pushes_a_second(
        self,
        api_client: APIClient,
        locmem_cache: LocMemCache,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # one instant: the posts never spread over two windows
        monkeypatch.setattr(SimpleRateThrottle, 'timer', lambda self: 1_000_000.0)
        config = baker.make(GlobalConfigWebhook, sender=WebhookSenderChoices.FIOAPI, is_active=True)
        url = reverse('data:fio-webhook-ingest', kwargs={'token': config.path})

        with patch('gamedata.api.viewsets.gamedata_process_fio_webhook.delay'):
            statuses = [api_client.post(url, {'Data': []}, format='json').status_code for _ in range(21)]

        assert statuses == [202] * 20 + [429]


class TestGetIdent:
    """NUM_PROXIES = 2: Cloudflare and Traefik each append one X-Forwarded-For entry."""

    @pytest.mark.parametrize(
        ('forwarded_for', 'ident'),
        [
            ('1.2.3.4', '1.2.3.4'),  # direct to Traefik, which replaces the header
            ('1.2.3.4, 172.64.0.1', '1.2.3.4'),  # through Cloudflare
            ('9.9.9.9, 1.2.3.4, 172.64.0.1', '1.2.3.4'),  # through Cloudflare, forged
            (None, '10.0.0.7'),  # no proxy: REMOTE_ADDR
        ],
    )
    def test_client_ip_for_each_header_shape(self, forwarded_for: str | None, ident: str) -> None:
        headers = {'X-Forwarded-For': forwarded_for} if forwarded_for else {}
        request = RequestFactory().get('/', headers=headers, REMOTE_ADDR='10.0.0.7')

        assert BaseThrottle().get_ident(request) == ident

    def test_forged_entries_share_one_scoped_bucket(self, api_client: APIClient, locmem_cache: LocMemCache) -> None:
        url = reverse('client-errors')
        body = {'kind': 'network', 'method': 'GET', 'path_template': '/data/storage/', 'release': '1.2.3'}

        def post(forwarded_for: str) -> int:
            return api_client.post(url, body, format='json', HTTP_X_FORWARDED_FOR=forwarded_for).status_code

        assert [post(f'9.9.9.{i}, 1.2.3.4, 172.64.0.1') for i in range(31)] == [204] * 30 + [429]
        assert post('1.2.3.5, 172.64.0.1') == 204
