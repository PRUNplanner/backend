from unittest.mock import patch

import httpx
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
        assert response.data['colorPalette'] == 'default'
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

    def test_update_persists_color_palette(self, api_client, user_factory):
        user = user_factory(id=1)

        response = api_client.as_user(user).patch(
            reverse('user:user_preferences'), data={'colorPalette': 'colorblind'}, format='json'
        )

        assert response.status_code == 200
        assert response.data['colorPalette'] == 'colorblind'
        assert UserPreference.objects.get(user=user).preferences['color_palette'] == 'colorblind'

    @pytest.mark.parametrize(
        'payload',
        [{'planOverrides': None}, {'layoutNavigationStyle': 'x'}, {'colorPalette': 'x'}, {'supplyCartDays': -1}],
    )
    def test_update_rejects_invalid_values(self, api_client, user_factory, payload):
        user = user_factory(id=1)

        response = api_client.as_user(user).patch(reverse('user:user_preferences'), data=payload, format='json')

        assert response.status_code == 400

    def test_retrieve_repairs_invalid_stored_values(self, api_client, user_factory):
        user = user_factory(id=1)
        baker.make(
            UserPreference,
            user=user,
            preferences={'plan_overrides': None, 'layout_navigation_style': 'x', 'color_palette': 'x'},
        )

        response = api_client.as_user(user).get(reverse('user:user_preferences'))

        assert response.status_code == 200
        assert response.data['planOverrides'] == {}
        assert response.data['layoutNavigationStyle'] == 'full'
        assert response.data['colorPalette'] == 'default'

    def test_update_round_trips_construction_built(self, api_client, user_factory):
        user = user_factory(id=1)
        payload = {
            'planOverrides': {
                'p1': {'includeCM': True, 'autoOptimizeHabs': False, 'constructionBuilt': {'FRM': 3, 'HB1': 0}}
            }
        }

        response = api_client.as_user(user).patch(reverse('user:user_preferences'), data=payload, format='json')

        assert response.status_code == 200
        override = response.data['planOverrides']['p1']
        assert override['constructionBuilt'] == {'FRM': 3, 'HB1': 0}
        assert override['includeCM'] is True
        assert override['autoOptimizeHabs'] is False
        stored = UserPreference.objects.get(user=user).preferences['plan_overrides']['p1']
        assert stored['construction_built'] == {'FRM': 3, 'HB1': 0}
        assert stored['include_cm'] is True

    @pytest.mark.parametrize(
        'built',
        [{'FRM': -1}, {'frm': 1}, {'ABCD': 1}, {'': 1}, {'F-1': 1}, {'FRM': 1.5}, {f'A{i}': 1 for i in range(101)}],
    )
    def test_update_rejects_invalid_construction_built(self, api_client, user_factory, built):
        user = user_factory(id=1)
        payload = {'planOverrides': {'p1': {'autoOptimizeHabs': True, 'constructionBuilt': built}}}

        response = api_client.as_user(user).patch(reverse('user:user_preferences'), data=payload, format='json')

        assert response.status_code == 400

    def test_update_accepts_100_construction_built_entries(self, api_client, user_factory):
        user = user_factory(id=1)
        built = {f'A{i}': 1 for i in range(100)}
        payload = {'planOverrides': {'p1': {'autoOptimizeHabs': True, 'constructionBuilt': built}}}

        response = api_client.as_user(user).patch(reverse('user:user_preferences'), data=payload, format='json')

        assert response.status_code == 200

    def test_construction_built_defaults_to_empty(self, api_client, user_factory):
        user = user_factory(id=1)
        baker.make(
            UserPreference,
            user=user,
            preferences={'plan_overrides': {'p1': {'include_cm': True, 'auto_optimize_habs': False}}},
        )

        response = api_client.as_user(user).get(reverse('user:user_preferences'))
        assert response.status_code == 200
        assert response.data['planOverrides']['p1']['constructionBuilt'] == {}
        assert response.data['planOverrides']['p1']['includeCM'] is True

        response = api_client.as_user(user).patch(
            reverse('user:user_preferences'),
            data={'planOverrides': {'p1': {'includeCM': True, 'autoOptimizeHabs': False}}},
            format='json',
        )
        assert response.status_code == 200
        assert response.data['planOverrides']['p1']['constructionBuilt'] == {}

    def test_update_accepts_decimal_supply_cart_days(self, api_client, user_factory):
        user = user_factory(id=1)

        response = api_client.as_user(user).patch(
            reverse('user:user_preferences'), data={'supplyCartDays': 2.5}, format='json'
        )

        assert response.status_code == 200
        assert response.data['supplyCartDays'] == 2.5
        assert UserPreference.objects.get(user=user).preferences['supply_cart_days'] == 2.5

    def test_retrieve_reads_stored_integer_supply_cart_days(self, api_client, user_factory):
        user = user_factory(id=1)
        baker.make(UserPreference, user=user, preferences={'supply_cart_days': 7})

        response = api_client.as_user(user).get(reverse('user:user_preferences'))

        assert response.status_code == 200
        assert response.data['supplyCartDays'] == 7

    def test_partial_update_keeps_omitted_keys(self, api_client, user_factory):
        user = user_factory(id=1)
        stored = {
            'locale': 'de_DE',
            'default_empire_uuid': '4b3c9d2e-0f1a-4b5c-8d7e-6f5a4b3c2d1e',
            'burn_days_red': 7,
            'burn_origin': 'Moria Station Warehouse',
            'layout_navigation_style': 'collapsed',
            'plan_overrides': {
                'p1': {'include_cm': True, 'visitation_material_exclusions': [], 'auto_optimize_habs': False}
            },
        }
        baker.make(UserPreference, user=user, preferences=stored)

        response = api_client.as_user(user).patch(
            reverse('user:user_preferences'), data={'burnDaysRed': 3}, format='json'
        )

        assert response.status_code == 200
        assert response.data['burnDaysRed'] == 3
        assert response.data['locale'] == 'de_DE'
        assert UserPreference.objects.get(user=user).preferences == {**stored, 'burn_days_red': 3}

    def test_partial_update_replaces_plan_overrides(self, api_client, user_factory):
        user = user_factory(id=1)
        override = {'include_cm': False, 'visitation_material_exclusions': [], 'auto_optimize_habs': True}
        baker.make(UserPreference, user=user, preferences={'plan_overrides': {'p1': override, 'p2': override}})

        response = api_client.as_user(user).patch(
            reverse('user:user_preferences'),
            data={'planOverrides': {'p2': {'includeCM': False, 'autoOptimizeHabs': True}}},
            format='json',
        )

        assert response.status_code == 200
        assert list(response.data['planOverrides']) == ['p2']
        assert list(UserPreference.objects.get(user=user).preferences['plan_overrides']) == ['p2']

    def test_partial_update_null_clears_default_uuid(self, api_client, user_factory):
        user = user_factory(id=1)
        baker.make(
            UserPreference, user=user, preferences={'default_empire_uuid': '4b3c9d2e-0f1a-4b5c-8d7e-6f5a4b3c2d1e'}
        )

        response = api_client.as_user(user).patch(
            reverse('user:user_preferences'), data={'defaultEmpireUuid': None}, format='json'
        )

        assert response.status_code == 200
        assert response.data['defaultEmpireUuid'] is None


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

    def test_register_silently_drops_duplicate_email(self, api_client, user_factory):
        with patch('user.tasks.send_email_verification_code.apply_async') as send_code:
            owner = user_factory(username='original_owner', email='newpilot@example.com')
            response = api_client.post(
                reverse('user:user_signup'), data=self._payload(username='second_pilot'), format='json'
            )

        assert response.status_code == 201
        assert response.data['username'] == 'second_pilot'
        # only the owner's own creation should have queued a code, not the second registration
        send_code.assert_called_once()

        user = User.objects.get(username='second_pilot')
        assert user.email is None

        owner.refresh_from_db()
        assert owner.email == 'newpilot@example.com'


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

    @pytest.mark.usefixtures('locmem_cache')
    def test_burst_of_refreshes_queues_the_post_refresh_task_once(self, api_client, user_factory):
        user = user_factory(id=1)
        other = user_factory(id=2)
        url = reverse('user:token_refresh')

        with patch('user.api.viewsets.user_handle_post_refresh.delay') as mock_delay:
            for _ in range(30):
                data = {'refresh': str(RefreshToken.for_user(user))}
                assert api_client.post(url, data=data, format='json').status_code == 200
            # the debounce is per user
            api_client.post(url, data={'refresh': str(RefreshToken.for_user(other))}, format='json')

        assert [call.args for call in mock_delay.call_args_list] == [(str(user.id),), (str(other.id),)]


class TestLoginView:
    def test_login_asks_for_a_fio_refresh(self, api_client):
        user = baker.make('user.User', username='pilot')
        user.set_password('secret-pass-1')
        user.save()

        with patch('user.api.urls.request_fio_refresh') as request_refresh:
            response = api_client.post(
                reverse('user:token_obtain_pair'),
                data={'username': 'pilot', 'password': 'secret-pass-1'},
                format='json',
            )

        assert response.status_code == 200
        request_refresh.assert_called_once_with(user.id, 'login')

    def test_failed_login_asks_for_nothing(self, api_client):
        with patch('user.api.urls.request_fio_refresh') as request_refresh:
            response = api_client.post(
                reverse('user:token_obtain_pair'), data={'username': 'nobody', 'password': 'wrong'}, format='json'
            )

        assert response.status_code == 401
        request_refresh.assert_not_called()


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

    def test_retrieve_has_the_fio_status(self, api_client, user_factory):
        user = user_factory(id=1, prun_username='Pilot', fio_apikey='key')

        response = api_client.as_user(user).get(reverse('user:user_profile'))

        assert (response.data['fio_status'], response.data['fio_last_refreshed_at']) == ('syncing', None)

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


AUTH_URL = 'https://rest.fnar.net/auth'


class TestUpdateProfileFioCheck:
    @staticmethod
    def _patch(api_client, user: User, data: dict[str, str | None]):
        with patch('gamedata.tasks.gamedata_refresh_user_fiodata.delay') as refresh:
            response = api_client.as_user(user).patch(reverse('user:user_profile'), data=data, format='json')
        return response, refresh

    @pytest.mark.parametrize('username', ['PilotName', 'pilotname', 'PILOTNAME'])
    def test_valid_pair_is_stored_and_refreshed_once(
        self, api_client, user_factory, httpx_mock, django_capture_on_commit_callbacks, username: str
    ):
        user = user_factory(id=1)
        httpx_mock.add_response(url=AUTH_URL, text='PILOTNAME')

        # the refresh is queued on commit, so the patch has to outlive the captured callbacks
        with (
            patch('gamedata.tasks.gamedata_refresh_user_fiodata.delay') as refresh,
            django_capture_on_commit_callbacks(execute=True),
        ):
            response, _ = self._patch(api_client, user, {'prun_username': username, 'fio_apikey': 'good'})

        assert response.status_code == 200
        assert response.data['fio_status'] == 'syncing'
        user.refresh_from_db()
        assert (user.prun_username, user.fio_apikey) == (username, 'good')
        refresh.assert_called_once_with(user.id)

    def test_wrong_key_is_rejected(self, api_client, user_factory, httpx_mock):
        user = user_factory(id=1)
        httpx_mock.add_response(url=AUTH_URL, status_code=401)

        response, refresh = self._patch(api_client, user, {'prun_username': 'Pilot', 'fio_apikey': 'bad'})

        assert (response.status_code, response.data) == (400, {'fio_apikey': ['fio_invalid_key']})
        user.refresh_from_db()
        assert (user.prun_username, user.fio_apikey) == (None, None)
        refresh.assert_not_called()

    def test_key_of_another_user_is_a_mismatch_without_the_owner(self, api_client, user_factory, httpx_mock):
        user = user_factory(id=1)
        httpx_mock.add_response(url=AUTH_URL, text='SOMEONEELSE')

        response, _ = self._patch(api_client, user, {'prun_username': 'Pilot', 'fio_apikey': 'good'})

        assert (response.status_code, response.data) == (400, {'prun_username': ['fio_username_mismatch']})
        assert b'SOMEONEELSE' not in response.content.upper()

    @pytest.mark.parametrize(
        'data, field',
        [
            ({'prun_username': 'Pilot', 'fio_apikey': ''}, 'fio_apikey'),
            ({'prun_username': 'Pilot'}, 'fio_apikey'),
            ({'prun_username': None, 'fio_apikey': 'key'}, 'prun_username'),
        ],
    )
    def test_one_field_alone_is_required(self, api_client, user_factory, httpx_mock, data, field: str):
        user = user_factory(id=1)

        response, _ = self._patch(api_client, user, data)

        assert (response.status_code, response.data) == (400, {field: ['fio_required']})
        assert httpx_mock.get_requests() == []

    def test_clearing_both_disconnects_without_fio(self, api_client, user_factory, httpx_mock):
        user = user_factory(id=1, prun_username='Pilot', fio_apikey='key')

        response, _ = self._patch(api_client, user, {'prun_username': '', 'fio_apikey': None})

        assert (response.status_code, response.data['fio_status']) == (200, 'none')
        assert httpx_mock.get_requests() == []

    @pytest.mark.parametrize('failure', ['timeout', 500, 503])
    def test_fio_unavailable_still_saves(self, api_client, user_factory, httpx_mock, caplog, failure):
        user = user_factory(id=1)
        if failure == 'timeout':
            httpx_mock.add_exception(httpx.ReadTimeout('slow'), url=AUTH_URL)
        else:
            httpx_mock.add_response(url=AUTH_URL, status_code=failure)

        response, _ = self._patch(api_client, user, {'prun_username': 'Pilot', 'fio_apikey': 'key'})

        assert response.status_code == 200
        user.refresh_from_db()
        assert user.fio_apikey == 'key'
        assert any(isinstance(r.msg, dict) and r.msg['event'] == 'fio_verify_unavailable' for r in caplog.records)

    def test_email_only_does_not_call_fio(self, api_client, user_factory, httpx_mock):
        user = user_factory(id=1, prun_username='Pilot', fio_apikey='key')

        with patch('user.tasks.send_email_verification_code.apply_async'):
            response, refresh = self._patch(
                api_client, user, {'prun_username': 'Pilot', 'fio_apikey': 'key', 'email': 'p@example.com'}
            )

        assert response.status_code == 200
        assert httpx_mock.get_requests() == []
        refresh.assert_not_called()

    def test_verify_log_has_the_result_but_not_the_key(self, api_client, user_factory, httpx_mock, caplog):
        user = user_factory(id=1)
        httpx_mock.add_response(url=AUTH_URL, text='PILOT')

        self._patch(api_client, user, {'prun_username': 'Pilot', 'fio_apikey': 'secret-key'})

        [line] = [r.msg for r in caplog.records if isinstance(r.msg, dict) and r.msg['event'] == 'fio_verify_completed']
        assert (line['result'], line['user_id']) == ('ok', user.id)
        assert 'secret-key' not in caplog.text

    @override_settings(CACHES=LOCMEM_CACHES)
    def test_update_profile_is_throttled_after_limit(self, api_client, user_factory):
        cache.clear()
        user = user_factory(id=1)

        for _ in range(throttle_limit('profile_update')):
            response, _ = self._patch(api_client, user, {'email': ''})
            assert response.status_code == 200

        response, _ = self._patch(api_client, user, {'email': ''})
        assert response.status_code == 429
        # reading the profile is not throttled by it
        assert api_client.as_user(user).get(reverse('user:user_profile')).status_code == 200
