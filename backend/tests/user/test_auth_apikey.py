from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker
from planning.models import PlanningPlan
from rest_framework import exceptions
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.tokens import AccessToken
from user.auth_apikey import LoggedJWTAuthentication, UserAPIKeyAuthentication
from user.models import UserAPIKey

pytestmark = pytest.mark.django_db


class TestUserAPIKeyAuthentication:
    @pytest.fixture
    def auth(self):
        return UserAPIKeyAuthentication()

    @pytest.fixture
    def factory(self):
        return APIRequestFactory()

    @pytest.fixture
    def user(self, db):
        from model_bakery import baker

        return baker.make('user.User')

    def _req(self, factory_request):
        """Helper to wrap request in DRF Request."""
        return Request(factory_request)

    @pytest.mark.parametrize(
        'header, query, expected_msg',
        [
            (None, None, None),  # Returns None (no key provided)
            (None, 'invalid.key', 'Invalid API Key'),  # DoesNotExist branch
        ],
    )
    def test_extraction_and_missing_cases(self, auth, factory, header, query, expected_msg):
        """Covers no-key (None) and non-existent key cases."""
        request = self._req(factory.get('/', {'api_key': query} if query else {}, HTTP_AUTHORIZATION=header or ''))

        if expected_msg is None:
            assert auth.authenticate(request) is None
        else:
            with pytest.raises(exceptions.AuthenticationFailed, match=expected_msg):
                auth.authenticate(request)

    @pytest.mark.parametrize(
        'offset_minutes, should_update',
        [
            (None, True),  # Case: last_used is None -> Update
            (30, False),  # Case: used 30m ago (< 1h) -> Skip
            (120, True),  # Case: used 2h ago (> 1h) -> Update
        ],
    )
    def test_timestamp_update_logic(self, auth, factory, user, offset_minutes, should_update):
        api_key_obj, plain_key = UserAPIKey.objects.create_key(name='test', user=user)

        # Setup initial state
        initial_time = timezone.now() - timedelta(minutes=offset_minutes) if offset_minutes else None
        UserAPIKey.objects.filter(pk=api_key_obj.pk).update(last_used=initial_time)
        user.last_login = initial_time
        user.save()

        request = self._req(factory.get('/', {'api_key': plain_key}))
        auth.authenticate(request)

        user.refresh_from_db()
        api_key_obj.refresh_from_db()

        if should_update:
            assert user.last_login != initial_time
            assert api_key_obj.last_used != initial_time
        else:
            assert user.last_login == initial_time
            # api key will always get the last_used update
            assert api_key_obj.last_used != initial_time

    def test_authenticate_extract_from_header_format(self, auth, factory, user):
        _, plain_key = UserAPIKey.objects.create_key(name='test', user=user)
        request = self._req(factory.get('/', HTTP_AUTHORIZATION=f'Api-Key {plain_key}'))

        result = auth.authenticate(request)
        assert result[0] == user

    @pytest.mark.parametrize('via', ['header', 'query'])
    def test_success_marks_the_django_request(self, auth, factory, user, via):
        api_key, plain_key = UserAPIKey.objects.create_key(name='test', user=user)
        if via == 'header':
            request = self._req(factory.get('/', HTTP_AUTHORIZATION=f'Api-Key {plain_key}'))
        else:
            request = self._req(factory.get('/', {'api_key': plain_key}))

        auth.authenticate(request)

        marks = (request._request._auth, request._request._api_key_prefix, request._request._api_key_via)
        assert marks == ('api_key', api_key.prefix, via)

    def test_unknown_key_marks_invalid(self, auth, factory):
        request = self._req(factory.get('/', {'api_key': 'nope.secret'}))

        with pytest.raises(exceptions.AuthenticationFailed, match='Invalid API Key'):
            auth.authenticate(request)
        assert request._request._auth_error == 'api_key_invalid'
        assert not hasattr(request._request, '_auth')

    def test_revoked_key_is_told_apart_from_an_unknown_one(self, auth, factory, user):
        api_key, plain_key = UserAPIKey.objects.create_key(name='test', user=user)
        api_key.revoked = True
        api_key.save()
        request = self._req(factory.get('/', HTTP_AUTHORIZATION=f'Api-Key {plain_key}'))

        with pytest.raises(exceptions.AuthenticationFailed, match='This API key has been revoked'):
            auth.authenticate(request)
        assert request._request._auth_error == 'api_key_revoked'

    def test_wrong_secret_for_a_revoked_prefix_is_invalid(self, auth, factory, user):
        api_key, plain_key = UserAPIKey.objects.create_key(name='test', user=user)
        api_key.revoked = True
        api_key.save()
        request = self._req(factory.get('/', {'api_key': f'{api_key.prefix}.wrong'}))

        with pytest.raises(exceptions.AuthenticationFailed, match='Invalid API Key'):
            auth.authenticate(request)
        assert request._request._auth_error == 'api_key_invalid'


class TestLoggedAuthentication:
    def test_jwt_marks_the_request(self):
        user = baker.make('user.User')
        request = Request(APIRequestFactory().get('/', HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}'))

        assert LoggedJWTAuthentication().authenticate(request)[0] == user
        assert request._request._auth == 'jwt'

    def test_no_credentials_leave_the_request_unmarked(self):
        request = Request(APIRequestFactory().get('/'))

        assert LoggedJWTAuthentication().authenticate(request) is None
        assert not hasattr(request._request, '_auth')


class TestUserAPIKeyAuthenticationCost:
    def test_recently_used_key_authenticates_with_a_single_read(self):
        from model_bakery import baker

        user = baker.make('user.User', last_login=timezone.now())
        api_key, key = UserAPIKey.objects.create_key(name='script', user=user)
        UserAPIKey.objects.filter(pk=api_key.pk).update(last_used=timezone.now())

        request = Request(APIRequestFactory().get('/', HTTP_AUTHORIZATION=f'Api-Key {key}'))

        with CaptureQueriesContext(connection) as ctx:
            authenticated_user, _ = UserAPIKeyAuthentication().authenticate(request)

        assert authenticated_user == user
        assert len(ctx.captured_queries) == 1


class TestKeyOfInactiveUserOrExpired:
    @pytest.mark.parametrize(
        ('is_active', 'expiry', 'ok'),
        [
            (True, None, True),
            (True, timedelta(days=1), True),
            (True, -timedelta(seconds=1), False),
            (False, None, False),
        ],
    )
    def test_key_works_only_for_an_active_user_before_expiry(self, is_active, expiry, ok):
        user = baker.make('user.User', is_active=is_active)
        _, key = UserAPIKey.objects.create_key(
            name='script', user=user, expiry_date=timezone.now() + expiry if expiry else None
        )
        request = Request(APIRequestFactory().get('/', {'api_key': key}))

        if ok:
            assert UserAPIKeyAuthentication().authenticate(request)[0] == user
        else:
            with pytest.raises(exceptions.AuthenticationFailed, match='Invalid API Key'):
                UserAPIKeyAuthentication().authenticate(request)
            assert request._request._auth_error == 'api_key_invalid'


class TestKeyCutoff:
    @pytest.mark.parametrize(
        ('cutoff_offset', 'ok'),
        [
            (None, True),  # no cutoff
            (timedelta(seconds=-10), True),  # key made after the cutoff
            (timedelta(seconds=10), False),  # key made before the cutoff (password change, reset, deactivation)
        ],
    )
    def test_key_against_the_cutoff(self, cutoff_offset, ok):
        user = baker.make('user.User')
        api_key, key = UserAPIKey.objects.create_key(name='script', user=user)
        if cutoff_offset is not None:
            user.tokens_valid_after = api_key.created + cutoff_offset
            user.save()
        request = Request(APIRequestFactory().get('/', {'api_key': key}))

        if ok:
            assert UserAPIKeyAuthentication().authenticate(request)[0] == user
        else:
            with pytest.raises(exceptions.AuthenticationFailed, match='Invalid API Key'):
                UserAPIKeyAuthentication().authenticate(request)
            assert request._request._auth_error == 'api_key_invalid'


class TestJWTCutoff:
    def _auth(self, token: AccessToken):
        request = Request(APIRequestFactory().get('/', HTTP_AUTHORIZATION=f'Bearer {token}'))
        return LoggedJWTAuthentication().authenticate(request)

    @pytest.mark.parametrize(
        ('cutoff_offset', 'ok'),
        [
            (None, True),  # no cutoff yet: tokens from before the migration keep working
            (timedelta(seconds=-10), True),  # token issued after the cutoff
            (timedelta(0), True),  # same second as the cutoff (a login right after the change)
            (timedelta(seconds=10), False),  # token issued before the cutoff
        ],
    )
    def test_access_token_against_the_cutoff(self, cutoff_offset, ok):
        user = baker.make('user.User')
        token = AccessToken.for_user(user)
        if cutoff_offset is not None:
            user.tokens_valid_after = datetime.fromtimestamp(token['iat'], tz=UTC) + cutoff_offset
            user.save()

        if ok:
            assert self._auth(token)[0] == user
        else:
            with pytest.raises(InvalidToken):
                self._auth(token)

    def test_cut_off_tokens_is_whole_seconds(self):
        user = baker.make('user.User')
        user.cut_off_tokens()

        assert user.tokens_valid_after is not None and user.tokens_valid_after.microsecond == 0

    def test_cutoff_check_reads_only_the_user_row(self, django_assert_num_queries):
        user = baker.make('user.User', tokens_valid_after=timezone.now() - timedelta(minutes=1))
        token = AccessToken.for_user(user)

        with django_assert_num_queries(1):
            assert self._auth(token)[0] == user


class TestKeyScope:
    """Through the real views: scope, ?api_key= and JWT-only endpoints (user/auth_apikey.py)."""

    @pytest.fixture
    def user(self):
        return baker.make('user.User', prun_username='PILOT', fio_apikey='fio-secret')

    @pytest.fixture
    def read_key(self, user):
        return UserAPIKey.objects.create_key(name='sheet', user=user)

    @pytest.fixture
    def write_key(self, user):
        return UserAPIKey.objects.create_key(name='script', user=user, scope=UserAPIKey.Scope.READ_WRITE)

    @staticmethod
    def _plan_payload(**overrides):
        from tests.fixtures.planning.fxt_plan_vallis import plan_data_vallis

        return {
            'plan_name': 'Keyed',
            'planet_natural_id': 'OT-580b',
            'plan_permits_used': 1,
            'plan_corphq': False,
            'plan_data': plan_data_vallis,
            **overrides,
        }

    @staticmethod
    def _header(key: str) -> dict[str, str]:
        return {'HTTP_AUTHORIZATION': f'Api-Key {key}'}

    def test_new_key_defaults_to_read(self, read_key):
        assert read_key[0].scope == 'read'

    def test_read_key_reads_but_cannot_write(self, api_client, read_key):

        url = reverse('planning:plan')
        assert api_client.get(url, **self._header(read_key[1])).status_code == 200

        response = api_client.post(url, data=self._plan_payload(), format='json', **self._header(read_key[1]))

        assert response.status_code == 403
        assert response.json() == {
            'detail': 'This API key is read-only. Create a read-write key to change data.',
            'code': 'api_key_read_only',
        }
        assert not PlanningPlan.objects.exists()

    def test_read_write_key_in_header_writes(self, api_client, write_key):
        header = self._header(write_key[1])

        created = api_client.post(reverse('planning:plan'), data=self._plan_payload(), format='json', **header)
        assert created.status_code == 201
        detail = reverse('planning:plan-detail', kwargs={'pk': created.json()['uuid']})
        updated = api_client.put(detail, data=self._plan_payload(plan_name='Renamed'), format='json', **header)
        assert updated.status_code == 200
        assert api_client.delete(detail, **header).status_code == 204

    def test_key_in_url_only_reads(self, api_client, write_key):

        url = reverse('planning:plan')
        query = f'{url}?api_key={write_key[1]}'
        assert api_client.get(query).status_code == 200

        response = api_client.post(query, data=self._plan_payload(), format='json')

        assert (response.status_code, response.json()['code']) == (403, 'api_key_in_url')
        assert not PlanningPlan.objects.exists()

    @pytest.mark.parametrize('scope', ['read', 'read_write'])
    @pytest.mark.parametrize(
        'method, route, body',
        [
            ('get', 'user:user_apikey_list', None),
            ('post', 'user:user_apikey_list', {'name': 'minted', 'scope': 'read_write'}),
            ('delete', 'user:user_apikey_detail', None),
            ('patch', 'user:user_profile', {'email': 'new@example.com', 'fio_apikey': ''}),
            ('post', 'user:user_change_password', {'old_password': 'x', 'new_password': 'Xk7!qzR9pLm2'}),
            ('post', 'user:user_request_email_verification', None),
            ('post', 'user:user_verify_email', {'code': '12345678'}),
        ],
    )
    def test_account_endpoints_refuse_every_key(self, api_client, user, scope, method, route, body):
        api_key, key = UserAPIKey.objects.create_key(name='k', user=user, scope=scope)
        url = reverse(route, kwargs={'pk': api_key.pk} if route.endswith('detail') else None)
        before = (UserAPIKey.objects.count(), user.email, user.fio_apikey, user.password)

        response = getattr(api_client, method)(url, data=body, format='json', **self._header(key))

        assert (response.status_code, response.json()['code']) == (403, 'api_key_not_allowed')
        user.refresh_from_db()
        assert (UserAPIKey.objects.count(), user.email, user.fio_apikey, user.password) == before

    def test_profile_hides_the_fio_key_from_a_key_only(self, api_client, user, read_key):
        url = reverse('user:user_profile')

        keyed = api_client.get(url, **self._header(read_key[1]))
        jwt = api_client.get(url, HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')

        assert (keyed.status_code, keyed.json()['fio_apikey']) == (200, None)
        assert (jwt.status_code, jwt.json()['fio_apikey']) == (200, 'fio-secret')

    def test_preferences_follow_the_scope(self, api_client, read_key, write_key):
        url = reverse('user:user_preferences')
        body = {'planOverrides': {}}

        assert api_client.get(url, **self._header(read_key[1])).status_code == 200
        assert api_client.patch(url, data=body, format='json', **self._header(read_key[1])).status_code == 403
        assert api_client.patch(url, data=body, format='json', **self._header(write_key[1])).status_code == 200

    def test_jwt_is_unaffected(self, api_client, user):
        header = {'HTTP_AUTHORIZATION': f'Bearer {AccessToken.for_user(user)}'}

        plan = api_client.post(reverse('planning:plan'), data=self._plan_payload(), format='json', **header)
        assert plan.status_code == 201
        created = api_client.post(reverse('user:user_apikey_list'), data={'name': 'n'}, format='json', **header)
        assert (created.status_code, created.json()['scope']) == (201, 'read')
        listed = api_client.get(reverse('user:user_apikey_list'), **header).json()
        assert [k['scope'] for k in listed] == ['read']

    def test_create_takes_a_scope_and_rejects_others(self, api_client, user):
        header = {'HTTP_AUTHORIZATION': f'Bearer {AccessToken.for_user(user)}'}
        url = reverse('user:user_apikey_list')

        created = api_client.post(url, data={'name': 'w', 'scope': 'read_write'}, format='json', **header)
        bad = api_client.post(url, data={'name': 'x', 'scope': 'admin'}, format='json', **header)

        assert (created.status_code, created.json()['scope']) == (201, 'read_write')
        assert UserAPIKey.objects.get(prefix=created.json()['prefix']).scope == 'read_write'
        assert (bad.status_code, list(bad.json())) == (400, ['scope'])

    @pytest.mark.parametrize(
        'scope, via, code',
        [
            ('read', 'header', 'api_key_read_only'),
            ('read_write', 'query', 'api_key_in_url'),
            ('read_write', 'header', None),
        ],
    )
    def test_request_finished_logs_scope_and_error(self, api_client, user, caplog, scope, via, code):
        _, key = UserAPIKey.objects.create_key(name='k', user=user, scope=scope)
        url = reverse('planning:plan')
        kwargs = self._header(key) if via == 'header' else {}
        if via == 'query':
            url = f'{url}?api_key={key}'

        api_client.post(url, data=self._plan_payload(), format='json', **kwargs)

        (line,) = [r.msg for r in caplog.records if isinstance(r.msg, dict) and r.msg['event'] == 'request_finished']
        assert (line['api_key_scope'], line.get('auth_error')) == (scope, code)

    def test_request_finished_logs_not_allowed(self, api_client, write_key, caplog):
        api_client.get(reverse('user:user_apikey_list'), **self._header(write_key[1]))

        (line,) = [r.msg for r in caplog.records if isinstance(r.msg, dict) and r.msg['event'] == 'request_finished']
        assert (line['api_key_scope'], line['auth_error']) == ('read_write', 'api_key_not_allowed')

    def test_created_log_carries_the_scope(self, api_client, user, caplog):
        header = {'HTTP_AUTHORIZATION': f'Bearer {AccessToken.for_user(user)}'}

        api_client.post(reverse('user:user_apikey_list'), data={'name': 'n'}, format='json', **header)

        (line,) = [r.msg for r in caplog.records if isinstance(r.msg, dict) and r.msg['event'] == 'api_key_created']
        assert line['api_key_scope'] == 'read'
