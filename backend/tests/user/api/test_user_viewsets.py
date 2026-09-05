from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from model_bakery import baker
from planning.models import PlanningCX, PlanningEmpire
from rest_framework_simplejwt.tokens import RefreshToken
from user.api.serializer import UserChangePasswordSerializer, UserProfileSerializer
from user.api.viewsets import UserProfileViewSet
from user.models import User, UserAPIKey, UserPreference
from user.models.verification_codes import VerificationCode, VerificationeCodeChoices

pytestmark = pytest.mark.django_db

# Test settings use DummyCache, which never throttles; ScopedRateThrottle needs a real cache backend.
LOCMEM_CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'throttle-test'}}


def throttle_limit(scope: str) -> int:
    """Number of requests allowed for a scope before ScopedRateThrottle returns 429."""
    rate = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'][scope]
    return int(rate.split('/')[0])


class TestAuthEndpointThrottling:
    def setup_method(self):
        cache.clear()

    @override_settings(CACHES=LOCMEM_CACHES)
    def test_login_is_throttled_after_limit(self, api_client):
        url = reverse('user:token_obtain_pair')

        for _ in range(throttle_limit('auth_login')):
            response = api_client.post(url, data={'username': 'nobody', 'password': 'wrong'}, format='json')
            assert response.status_code == 401

        response = api_client.post(url, data={'username': 'nobody', 'password': 'wrong'}, format='json')
        assert response.status_code == 429

    @override_settings(CACHES=LOCMEM_CACHES)
    def test_register_is_throttled_after_limit(self, api_client):
        url = reverse('user:user_signup')

        for _ in range(throttle_limit('auth_register')):
            response = api_client.post(url, data={}, format='json')
            assert response.status_code == 400

        response = api_client.post(url, data={}, format='json')
        assert response.status_code == 429

    @override_settings(CACHES=LOCMEM_CACHES)
    def test_request_email_verification_is_throttled_after_limit(self, api_client, user_factory):
        user = user_factory(id=1, is_email_verified=True)
        url = reverse('user:user_request_email_verification')

        for _ in range(throttle_limit('auth_verify_email')):
            response = api_client.as_user(user).post(url)
            assert response.status_code == 400

        response = api_client.as_user(user).post(url)
        assert response.status_code == 429

    @override_settings(CACHES=LOCMEM_CACHES)
    def test_password_reset_request_is_throttled_after_limit(self, api_client):
        url = reverse('user:user_request_password_reset')

        for _ in range(throttle_limit('auth_password_reset')):
            response = api_client.post(url, data={'email': 'nobody@example.com'}, format='json')
            assert response.status_code == 200

        response = api_client.post(url, data={'email': 'nobody@example.com'}, format='json')
        assert response.status_code == 429


class TestUserPreferenceViewSet:
    def test_retrieve_requires_auth(self, api_client):
        response = api_client.get(reverse('user:user_preferences'))
        assert response.status_code == 401

    def test_retrieve_returns_defaults_when_unset(self, api_client, user_factory):
        user = user_factory(id=1)

        response = api_client.as_user(user).get(reverse('user:user_preferences'))

        assert response.status_code == 200
        assert response.data['locale'] == 'en_US'
        assert response.data['burnDaysRed'] == 5
        assert UserPreference.objects.filter(user=user).exists()

    def test_update_persists_preferences(self, api_client, user_factory):
        user = user_factory(id=1)

        response = api_client.as_user(user).patch(
            reverse('user:user_preferences'), data={'locale': 'de_DE', 'burnDaysRed': 3}, format='json'
        )

        assert response.status_code == 200
        assert response.data['locale'] == 'de_DE'
        assert response.data['burnDaysRed'] == 3

        preference = UserPreference.objects.get(user=user)
        assert preference.preferences['locale'] == 'de_DE'


class TestUserRegisterViewSet:
    def _payload(self, **overrides):
        payload = {
            'username': 'newpilot',
            'password': 'Xk7!qzR9pLm2',
            'email': 'newpilot@example.com',
            'planet_id': 'OT-580b',
            'planet_input': 'montem',
        }
        payload.update(overrides)
        return payload

    def test_register_creates_user_cx_and_empire(self, api_client):
        with patch('user.tasks.send_email_verification_code.apply_async'):
            response = api_client.post(reverse('user:user_signup'), data=self._payload(), format='json')

        assert response.status_code == 201
        assert response.data['username'] == 'newpilot'

        user = User.objects.get(username='newpilot')
        assert PlanningCX.objects.filter(user=user).exists()
        assert PlanningEmpire.objects.filter(user=user).exists()

    def test_register_rejects_wrong_planet_captcha(self, api_client):
        response = api_client.post(reverse('user:user_signup'), data=self._payload(planet_input='wrong'), format='json')
        assert response.status_code == 400

    def test_register_rejects_duplicate_username(self, api_client, user_factory):
        user_factory(username='newpilot')

        response = api_client.post(reverse('user:user_signup'), data=self._payload(), format='json')
        assert response.status_code == 400


class TestUserAPIKeyViewSet:
    def test_list_requires_auth(self, api_client):
        response = api_client.get(reverse('user:user_apikey_list'))
        assert response.status_code == 401

    def test_create_returns_key_material_once(self, api_client, user_factory):
        user = user_factory(id=1)

        response = api_client.as_user(user).post(
            reverse('user:user_apikey_list'), data={'name': 'my key'}, format='json'
        )

        assert response.status_code == 201
        assert response.data['name'] == 'my key'
        assert 'api_key' in response.data and response.data['api_key']

    def test_list_only_returns_own_keys(self, api_client, user_factory):
        user = user_factory(id=1)
        other_user = user_factory(id=2)

        UserAPIKey.objects.create_key(name='mine', user=user)
        UserAPIKey.objects.create_key(name='theirs', user=other_user)

        response = api_client.as_user(user).get(reverse('user:user_apikey_list'))

        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]['name'] == 'mine'

    def test_destroy_own_key(self, api_client, user_factory):
        user = user_factory(id=1)

        api_key, _key = UserAPIKey.objects.create_key(name='mine', user=user)

        url = reverse('user:user_apikey_detail', kwargs={'pk': api_key.id})

        response_noauth = api_client.delete(url)
        assert response_noauth.status_code == 401

        response = api_client.as_user(user).delete(url)
        assert response.status_code == 204
        assert not UserAPIKey.objects.filter(id=api_key.id).exists()

    def test_destroy_other_users_key_returns_404(self, api_client, user_factory):
        user = user_factory(id=1)
        other_user = user_factory(id=2)

        api_key, _key = UserAPIKey.objects.create_key(name='theirs', user=other_user)

        url = reverse('user:user_apikey_detail', kwargs={'pk': api_key.id})
        response = api_client.as_user(user).delete(url)

        assert response.status_code == 404


class TestUserEmailVerificationViewSet:
    def test_request_code_requires_auth(self, api_client):
        response = api_client.post(reverse('user:user_request_email_verification'))
        assert response.status_code == 401

    def test_request_code_already_verified_returns_400(self, api_client, user_factory):
        user = user_factory(id=1, is_email_verified=True)

        response = api_client.as_user(user).post(reverse('user:user_request_email_verification'))

        assert response.status_code == 400

    def test_request_code_sends_email_for_unverified_user(self, api_client, user_factory):
        with patch('user.tasks.send_email_verification_code.apply_async') as mock_apply_async:
            user = user_factory(id=1, is_email_verified=False, email='pilot@example.com')
            mock_apply_async.reset_mock()

            response = api_client.as_user(user).post(reverse('user:user_request_email_verification'))

        assert response.status_code == 200
        mock_apply_async.assert_called_once()
        assert VerificationCode.objects.filter(user=user, purpose=VerificationeCodeChoices.EMAIL_VERIFICATION).exists()

    def test_verify_email_with_valid_code(self, api_client, user_factory):
        user = user_factory(id=1, is_email_verified=False)
        baker.make(
            'user.VerificationCode', user=user, code='ABCD1234', purpose=VerificationeCodeChoices.EMAIL_VERIFICATION
        )

        response = api_client.as_user(user).post(
            reverse('user:user_verify_email'), data={'code': 'abcd1234'}, format='json'
        )

        assert response.status_code == 200
        user.refresh_from_db()
        assert user.is_email_verified is True

    def test_verify_email_with_invalid_code_returns_400(self, api_client, user_factory):
        user = user_factory(id=1, is_email_verified=False)
        baker.make(
            'user.VerificationCode', user=user, code='ABCD1234', purpose=VerificationeCodeChoices.EMAIL_VERIFICATION
        )

        response = api_client.as_user(user).post(
            reverse('user:user_verify_email'), data={'code': 'wrongcod'}, format='json'
        )

        assert response.status_code == 400

    def test_verify_email_with_malformed_code_returns_serializer_errors(self, api_client, user_factory):
        user = user_factory(id=1)

        response = api_client.as_user(user).post(
            reverse('user:user_verify_email'), data={'code': 'short'}, format='json'
        )

        assert response.status_code == 400
        assert 'code' in response.data


class TestCustomTokenRefreshView:
    def test_refresh_with_valid_token_queues_post_refresh_task(self, api_client, user_factory):
        user = user_factory(id=1)
        refresh = RefreshToken.for_user(user)

        with patch('user.api.viewsets.user_handle_post_refresh.delay') as mock_delay:
            response = api_client.post(reverse('user:token_refresh'), data={'refresh': str(refresh)}, format='json')

        assert response.status_code == 200
        assert 'access' in response.data
        mock_delay.assert_called_once_with(str(user.id))

    def test_refresh_with_invalid_token_returns_401(self, api_client):
        with patch('user.api.viewsets.user_handle_post_refresh.delay') as mock_delay:
            response = api_client.post(
                reverse('user:token_refresh'), data={'refresh': 'not-a-real-token'}, format='json'
            )

        assert response.status_code == 401
        mock_delay.assert_not_called()


class TestUserPasswordResetViewSet:
    def test_request_code_for_known_verified_user(self, api_client, user_factory):
        user = user_factory(id=1, email='pilot@example.com', is_email_verified=True)

        with patch('user.tasks.send_password_reset_code.apply_async') as mock_apply_async:
            response = api_client.post(
                reverse('user:user_request_password_reset'), data={'email': user.email}, format='json'
            )

        assert response.status_code == 200
        mock_apply_async.assert_called_once()

    def test_request_code_for_unknown_email_does_not_send(self, api_client):
        with patch('user.tasks.send_password_reset_code.apply_async') as mock_apply_async:
            response = api_client.post(
                reverse('user:user_request_password_reset'), data={'email': 'nobody@example.com'}, format='json'
            )

        assert response.status_code == 200
        mock_apply_async.assert_not_called()

    def test_password_reset_with_valid_code(self, api_client, user_factory):
        user = user_factory(id=1, email='pilot@example.com', is_email_verified=True)
        baker.make('user.VerificationCode', user=user, code='RESET123', purpose=VerificationeCodeChoices.PASSWORD_RESET)

        response = api_client.post(
            reverse('user:user_password_reset'),
            data={'email': user.email, 'code': 'RESET123', 'new_password': 'Xk7!qzR9pLm2'},
            format='json',
        )

        assert response.status_code == 200
        user.refresh_from_db()
        assert user.check_password('Xk7!qzR9pLm2')

    def test_password_reset_with_invalid_code_returns_400(self, api_client, user_factory):
        user = user_factory(id=1, email='pilot@example.com', is_email_verified=True)

        response = api_client.post(
            reverse('user:user_password_reset'),
            data={'email': user.email, 'code': 'WRONGCOD', 'new_password': 'Xk7!qzR9pLm2'},
            format='json',
        )

        assert response.status_code == 400


class TestUserProfileViewSet:
    def test_retrieve_requires_auth(self, api_client):
        response = api_client.get(reverse('user:user_profile'))
        assert response.status_code == 401

    def test_retrieve_returns_profile(self, api_client, user_factory):
        user = user_factory(id=1, username='pilot')

        response = api_client.as_user(user).get(reverse('user:user_profile'))

        assert response.status_code == 200
        assert response.data['username'] == 'pilot'

    def test_update_profile(self, api_client, user_factory):
        user = user_factory(id=1)

        response = api_client.as_user(user).patch(
            reverse('user:user_profile'), data={'prun_username': 'PilotName'}, format='json'
        )

        assert response.status_code == 200
        user.refresh_from_db()
        assert user.prun_username == 'PilotName'

    def test_change_password_wrong_old_password_returns_400(self, api_client, user_factory):
        user = user_factory(id=1)
        user.set_password('CorrectHorse1!')
        user.save()

        response = api_client.as_user(user).post(
            reverse('user:user_change_password'),
            data={'old_password': 'WrongPassword', 'new_password': 'Xk7!qzR9pLm2'},
            format='json',
        )

        assert response.status_code == 400

    def test_change_password_success(self, api_client, user_factory):
        user = user_factory(id=1)
        user.set_password('CorrectHorse1!')
        user.save()

        response = api_client.as_user(user).post(
            reverse('user:user_change_password'),
            data={'old_password': 'CorrectHorse1!', 'new_password': 'Xk7!qzR9pLm2'},
            format='json',
        )

        assert response.status_code == 200
        user.refresh_from_db()
        assert user.check_password('Xk7!qzR9pLm2')

    def test_get_serializer_class_depends_on_action(self):
        viewset = UserProfileViewSet()

        viewset.action = 'change_password'
        assert viewset.get_serializer_class() is UserChangePasswordSerializer

        viewset.action = 'retrieve'
        assert viewset.get_serializer_class() is UserProfileSerializer
