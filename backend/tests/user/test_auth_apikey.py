from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from user.auth_apikey import UserAPIKeyAuthentication
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

    def test_authenticate_revoked_key_logic(self, auth, factory, user):
        """Forces coverage of the 'if api_key.revoked' branch using a mock."""
        api_key_obj, plain_key = UserAPIKey.objects.create_key(name='test', user=user)
        api_key_obj.revoked = True
        api_key_obj.save()

        request = self._req(factory.get('/', {'api_key': plain_key}))

        with patch('user.models.UserAPIKey.objects.get_from_key', return_value=api_key_obj):
            with pytest.raises(exceptions.AuthenticationFailed, match='This API key has been revoked'):
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
            assert api_key_obj.last_used == initial_time

    def test_authenticate_extract_from_header_format(self, auth, factory, user):
        _, plain_key = UserAPIKey.objects.create_key(name='test', user=user)
        request = self._req(factory.get('/', HTTP_AUTHORIZATION=f'Api-Key {plain_key}'))

        result = auth.authenticate(request)
        assert result[0] == user
