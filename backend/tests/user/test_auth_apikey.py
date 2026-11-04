from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from model_bakery import baker
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
