from collections.abc import Mapping, MutableMapping
from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker
from planning.models import PlanningCX, PlanningEmpire
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from structlog.testing import capture_logs
from user.api.serializer import UserChangePasswordSerializer, UserProfileSerializer, UserRegisterSerializer
from user.api.viewsets import UserProfileViewSet
from user.models import User, UserAPIKey, UserPreference
from user.models.verification_codes import EXPIRY_TIME, VerificationCode, VerificationeCodeChoices

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

        with capture_logs() as logs:
            response = api_client.post(url, data={}, format='json')
        assert response.status_code == 429
        assert not [e for e in logs if e['event'].startswith('user_signup_')]

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

    def test_plan_suggestions_defaults_true_for_stored_preferences_without_it(self, api_client, user_factory):
        user = user_factory(id=1)
        baker.make(UserPreference, user=user, preferences={'locale': 'de_DE'})

        response = api_client.as_user(user).get(reverse('user:user_preferences'))

        assert response.status_code == 200
        assert response.data['planSuggestions'] is True

    def test_update_plan_suggestions_keeps_other_keys(self, api_client, user_factory):
        user = user_factory(id=1)
        baker.make(UserPreference, user=user, preferences={'locale': 'de_DE', 'burn_days_red': 3})

        response = api_client.as_user(user).patch(
            reverse('user:user_preferences'), data={'planSuggestions': False}, format='json'
        )

        assert response.status_code == 200
        assert response.data['planSuggestions'] is False
        stored = UserPreference.objects.get(user=user).preferences
        assert stored['plan_suggestions'] is False
        assert (stored['locale'], stored['burn_days_red']) == ('de_DE', 3)

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

    def test_partial_update_merges_plan_overrides_per_plan(self, api_client, user_factory):
        user = user_factory(id=1)
        override = {'include_cm': False, 'visitation_material_exclusions': [], 'auto_optimize_habs': True}
        baker.make(UserPreference, user=user, preferences={'plan_overrides': {'p1': override, 'p2': override}})

        response = api_client.as_user(user).patch(
            reverse('user:user_preferences'),
            data={'planOverrides': {'p2': {'includeCM': True, 'autoOptimizeHabs': True}}},
            format='json',
        )

        assert response.status_code == 200
        assert list(response.data['planOverrides']) == ['p1', 'p2']
        stored = UserPreference.objects.get(user=user).preferences['plan_overrides']
        assert stored['p1'] == override
        assert stored['p2']['include_cm'] is True

    def test_partial_update_null_plan_override_deletes_only_that_plan(self, api_client, user_factory):
        user = user_factory(id=1)
        override = {'include_cm': False, 'visitation_material_exclusions': [], 'auto_optimize_habs': True}
        baker.make(UserPreference, user=user, preferences={'plan_overrides': {'p1': override, 'p2': override}})

        response = api_client.as_user(user).patch(
            reverse('user:user_preferences'), data={'planOverrides': {'p1': None, 'p3': None}}, format='json'
        )

        assert response.status_code == 200
        assert list(response.data['planOverrides']) == ['p2']
        assert UserPreference.objects.get(user=user).preferences['plan_overrides'] == {'p2': override}

    def test_partial_update_one_key_keeps_the_others(self, api_client, user_factory):
        user = user_factory(id=1)
        baker.make(UserPreference, user=user, preferences={'burn_days_red': 2, 'burn_days_yellow': 10})

        response = api_client.as_user(user).patch(
            reverse('user:user_preferences'), data={'burnDaysYellow': 7}, format='json'
        )

        assert response.status_code == 200
        assert UserPreference.objects.get(user=user).preferences == {'burn_days_red': 2, 'burn_days_yellow': 7}

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

    def _signup(
        self, api_client, **overrides: str
    ) -> tuple[Response, list[MutableMapping[str, object]], list[MutableMapping[str, object]]]:
        """Post a signup; returns the response, all captured log events and the signup events."""
        with patch('user.tasks.send_email_verification_code.apply_async'), capture_logs() as logs:
            response = api_client.post(reverse('user:user_signup'), data=self._payload(**overrides), format='json')
        return response, logs, [e for e in logs if str(e['event']).startswith('user_signup_')]

    def test_register_logs_completed(self, api_client):
        response, _, events = self._signup(api_client)

        assert response.status_code == 201
        assert response.data == {'username': 'newpilot'}
        user = User.objects.get(username='newpilot')
        assert events == [
            {'event': 'user_signup_completed', 'user_id': user.pk, 'has_email': True, 'log_level': 'info'}
        ]

    def test_register_without_email_logs_has_email_false(self, api_client):
        _, _, events = self._signup(api_client, email='')

        assert [(e['event'], e['has_email']) for e in events] == [('user_signup_completed', False)]

    @pytest.mark.parametrize(
        ('overrides', 'codes'),
        [
            ({'username': 'taken'}, ['username:username_taken']),
            ({'planet_input': 'wrong'}, ['planet_input:planet_mismatch']),
            ({'password': '12345678'}, ['password:password_entirely_numeric', 'password:password_too_common']),
            ({'password': 'short'}, ['password:min_length', 'password:password_too_short']),
            # DRF runs validate() (the planet check) only once every field passed
            (
                {'username': 'taken', 'planet_input': 'wrong', 'password': '12345678'},
                ['password:password_entirely_numeric', 'password:password_too_common', 'username:username_taken'],
            ),
        ],
    )
    def test_register_logs_rejected_codes(self, api_client, user_factory, overrides: dict[str, str], codes: list[str]):
        user_factory(username='taken')

        response, _, events = self._signup(api_client, **overrides)

        assert response.status_code == 400
        assert len(events) == 1
        assert events[0]['event'] == 'user_signup_rejected'
        assert events[0]['codes'] == codes
        assert events[0]['fields'] == sorted({c.split(':')[0] for c in codes})
        # planet_id only on planet mismatches
        assert ('planet_id' in events[0]) == ('planet_input:planet_mismatch' in codes)

    def test_register_rejected_planet_logs_planet_id(self, api_client):
        _, _, events = self._signup(api_client, planet_id='KW-020c', planet_input='montem')

        assert events[0]['planet_id'] == 'KW-020c'

    def test_register_rejected_unknown_planet_id_is_not_logged(self, api_client):
        _, _, events = self._signup(api_client, planet_id='free text from the client')

        assert events[0]['codes'] == ['planet_input:planet_mismatch']
        assert 'planet_id' not in events[0]

    def test_register_rejected_body_is_unchanged(self, api_client, user_factory):
        user_factory(username='taken')

        response, _, _ = self._signup(api_client, username='taken', planet_input='wrong', password='12345678')

        # captured before the logging change
        assert response.data == {
            'username': ['This username is already taken.'],
            'password': ['This password is too common.', 'This password is entirely numeric.'],
        }

    @pytest.mark.parametrize(
        'overrides',
        [
            {},
            {'username': 'occupied_pilot'},
            {'planet_input': 'Wrongplanet', 'email': 'secret-mail@example.com'},
            {'password': 'Sup3rSecretPw!'},
        ],
    )
    def test_register_logs_no_submitted_values(self, api_client, user_factory, overrides: dict[str, str]):
        user_factory(username='occupied_pilot')
        payload = self._payload(**overrides)

        _, logs, events = self._signup(api_client, **overrides)

        assert len(events) == 1
        logged = repr(logs).lower()
        for field in ('username', 'password', 'email', 'planet_input'):
            assert payload[field].lower() not in logged, field

    def test_planet_mismatch_error_carries_code(self):
        serializer = UserRegisterSerializer(data=self._payload(planet_input='wrong'))

        assert not serializer.is_valid()
        assert serializer.errors['planet_input'][0].code == 'planet_mismatch'

    def test_register_silently_drops_duplicate_email(
        self, api_client, user_factory, django_capture_on_commit_callbacks
    ):
        with (
            patch('user.tasks.send_email_verification_code.apply_async') as send_code,
            django_capture_on_commit_callbacks(execute=True),
        ):
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


def _backdate(code: VerificationCode, age: timedelta) -> None:
    # created_at is auto_now_add, so backdate after creation
    VerificationCode.objects.filter(pk=code.pk).update(created_at=timezone.now() - age)


def _make_code(user: User, purpose: VerificationeCodeChoices, code: str) -> VerificationCode:
    return baker.make('user.VerificationCode', user=user, code=code, purpose=purpose)


class TestUserEmailVerificationViewSet:
    def test_request_code_requires_auth(self, api_client):
        response = api_client.post(reverse('user:user_request_email_verification'))
        assert response.status_code == 401

    def test_request_code_without_email_returns_no_email(
        self, api_client, user_factory, django_capture_on_commit_callbacks
    ):
        user = user_factory(id=1)

        with django_capture_on_commit_callbacks() as callbacks:
            response = api_client.as_user(user).post(reverse('user:user_request_email_verification'))

        assert response.status_code == 400
        assert response.data['code'] == 'no_email'
        assert callbacks == []
        assert not VerificationCode.objects.filter(user=user).exists()

    def test_request_code_already_verified_returns_400(self, api_client, user_factory):
        user = user_factory(id=1, email='pilot@example.com', is_email_verified=True)

        response = api_client.as_user(user).post(reverse('user:user_request_email_verification'))

        assert response.status_code == 400
        assert response.data == {'detail': 'Email already verified.', 'code': 'already_verified'}

    def test_request_code_replaces_the_previous_code(
        self, api_client, user_factory, django_capture_on_commit_callbacks
    ):
        user = user_factory(id=1, email='pilot@example.com', is_email_verified=False)
        (previous,) = VerificationCode.objects.filter(user=user)

        with (
            patch('user.tasks.send_email_verification_code.apply_async') as send,
            django_capture_on_commit_callbacks(execute=True),
        ):
            response = api_client.as_user(user).post(reverse('user:user_request_email_verification'))

        assert response.status_code == 200
        assert response.data['code'] == 'sent'
        send.assert_called_once()
        (code,) = VerificationCode.objects.filter(user=user, purpose=VerificationeCodeChoices.EMAIL_VERIFICATION)
        assert code.pk != previous.pk
        assert send.call_args.kwargs['args'][3] == code.code

    def test_verify_email_with_valid_code(self, api_client, user_factory):
        user = user_factory(id=1, is_email_verified=False)
        code = _make_code(user, VerificationeCodeChoices.EMAIL_VERIFICATION, 'ABCD1234')
        client = api_client.as_user(user)

        response = client.post(reverse('user:user_verify_email'), data={'code': 'abcd1234'}, format='json')

        assert response.status_code == 200
        assert response.data == {'detail': 'Email verified.', 'code': 'verified'}
        user.refresh_from_db()
        code.refresh_from_db()
        assert user.is_email_verified is True
        assert code.is_used is True

        again = client.post(reverse('user:user_verify_email'), data={'code': 'ABCD1234'}, format='json')
        assert again.status_code == 400
        assert again.data['code'] == 'already_verified'

    @pytest.mark.parametrize(
        ('code_input', 'age', 'expected'),
        [
            ('WRONGCOD', timedelta(0), 'invalid'),
            ('ÄÄÄÄÄÄÄÄ', timedelta(0), 'invalid'),
            ('ABCD1234', timedelta(days=30), 'expired'),
        ],
        ids=['wrong', 'non-ascii', 'expired'],
    )
    def test_verify_email_failures(self, api_client, user_factory, code_input: str, age: timedelta, expected: str):
        user = user_factory(id=1, is_email_verified=False)
        _backdate(_make_code(user, VerificationeCodeChoices.EMAIL_VERIFICATION, 'ABCD1234'), age)

        response = api_client.as_user(user).post(
            reverse('user:user_verify_email'), data={'code': code_input}, format='json'
        )

        assert response.status_code == 400
        assert response.data['code'] == expected
        user.refresh_from_db()
        assert user.is_email_verified is False

    def test_verify_email_without_a_code_returns_no_code(self, api_client, user_factory):
        user = user_factory(id=1, is_email_verified=False)

        response = api_client.as_user(user).post(
            reverse('user:user_verify_email'), data={'code': 'ABCD1234'}, format='json'
        )

        assert response.status_code == 400
        assert response.data['code'] == 'no_code'

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


RESET_REQUESTED = {
    'detail': 'If your email is valid and attached to a PRUNplanner user, please check your inbox.',
    'code': 'requested',
}


class TestUserPasswordResetViewSet:
    @pytest.mark.parametrize(
        ('typed', 'verified', 'sends'),
        [
            ('pilot@example.com', True, True),
            ('PILOT@Example.COM', True, True),
            ('pilot@example.com', False, False),
            ('nobody@example.com', True, False),
        ],
        ids=['confirmed', 'confirmed-other-case', 'unconfirmed', 'unknown'],
    )
    def test_request_code_answers_the_same_and_sends_only_to_confirmed(
        self, api_client, user_factory, django_capture_on_commit_callbacks, typed: str, verified: bool, sends: bool
    ):
        user = user_factory(id=1, email='pilot@example.com', is_email_verified=verified)

        with (
            patch('user.tasks.send_password_reset_code.apply_async') as send,
            django_capture_on_commit_callbacks(execute=True),
        ):
            response = api_client.post(
                reverse('user:user_request_password_reset'), data={'email': typed}, format='json'
            )

        assert response.status_code == 200
        assert response.data == RESET_REQUESTED
        assert send.called is sends
        assert (
            VerificationCode.objects.filter(user=user, purpose=VerificationeCodeChoices.PASSWORD_RESET).exists()
            is sends
        )

    def test_password_reset_with_valid_code(self, api_client, user_factory):
        user = user_factory(id=1, email='pilot@example.com', is_email_verified=True)
        code = _make_code(user, VerificationeCodeChoices.PASSWORD_RESET, 'RESET123')
        other = _make_code(user, VerificationeCodeChoices.PASSWORD_RESET, 'OTHER123')

        response = api_client.post(
            reverse('user:user_password_reset'),
            data={'email': 'Pilot@EXAMPLE.com', 'code': 'reset123', 'new_password': 'Xk7!qzR9pLm2'},
            format='json',
        )

        assert response.status_code == 200
        assert response.data == {'detail': 'Password successfully reset.', 'code': 'reset'}
        code.refresh_from_db()
        assert code.is_used is True
        assert not VerificationCode.objects.filter(pk=other.pk).exists()
        login = api_client.post(
            reverse('user:token_obtain_pair'), data={'username': user.username, 'password': 'Xk7!qzR9pLm2'}
        )
        assert login.status_code == 200

    @pytest.mark.parametrize(
        ('verified', 'code_input', 'age', 'expected'),
        [
            (True, 'WRONGCOD', timedelta(0), 'invalid'),
            (True, 'ÄÄÄÄÄÄÄÄ', timedelta(0), 'invalid'),
            (False, 'RESET123', timedelta(0), 'invalid'),
            (True, 'RESET123', EXPIRY_TIME[VerificationeCodeChoices.PASSWORD_RESET] + timedelta(minutes=1), 'expired'),
        ],
        ids=['wrong', 'non-ascii', 'unconfirmed', 'expired'],
    )
    def test_password_reset_failures(
        self, api_client, user_factory, verified: bool, code_input: str, age: timedelta, expected: str
    ):
        user = user_factory(id=1, email='pilot@example.com', is_email_verified=verified)
        _backdate(_make_code(user, VerificationeCodeChoices.PASSWORD_RESET, 'RESET123'), age)

        response = api_client.post(
            reverse('user:user_password_reset'),
            data={'email': user.email, 'code': code_input, 'new_password': 'Xk7!qzR9pLm2'},
            format='json',
        )

        assert response.status_code == 400
        assert response.data['code'] == expected
        user.refresh_from_db()
        assert not user.check_password('Xk7!qzR9pLm2')

    def test_password_reset_keeps_field_errors_for_a_weak_password(self, api_client, user_factory):
        user = user_factory(id=1, email='pilot@example.com', is_email_verified=True)
        _make_code(user, VerificationeCodeChoices.PASSWORD_RESET, 'RESET123')

        response = api_client.post(
            reverse('user:user_password_reset'),
            data={'email': user.email, 'code': 'RESET123', 'new_password': '123'},
            format='json',
        )

        assert response.status_code == 400
        assert 'new_password' in response.data


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


class TestFIORepairPreferences:
    SITE_A = 'a' * 32
    SITE_B = 'b' * 32
    PLAN = '11111111-1111-1111-1111-111111111111'

    def _patch(self, api_client, user: User, payload: Mapping[str, object]):
        return api_client.as_user(user).patch(reverse('user:user_preferences'), data=payload, format='json')

    def test_retrieve_returns_defaults_for_stored_preferences_without_them(self, api_client, user_factory):
        user = user_factory(id=1)
        baker.make(UserPreference, user=user, preferences={'locale': 'de_DE', 'fio_repair_plan_links': None})

        response = api_client.as_user(user).get(reverse('user:user_preferences'))

        assert response.status_code == 200
        assert response.data['fioRepairIntervalDays'] == 60
        assert response.data['fioRepairDueSoonDays'] == 7
        assert response.data['fioRepairArrivalDays'] == 2
        assert response.data['fioRepairPlanLinks'] == {}

    def test_update_persists_settings(self, api_client, user_factory):
        user = user_factory(id=1)
        payload = {'fioRepairIntervalDays': 180, 'fioRepairDueSoonDays': 0, 'fioRepairArrivalDays': 30}

        response = self._patch(api_client, user, payload)

        assert response.status_code == 200
        assert {k: response.data[k] for k in payload} == payload
        stored = UserPreference.objects.get(user=user).preferences
        assert (stored['fio_repair_interval_days'], stored['fio_repair_arrival_days']) == (180, 30)

    @pytest.mark.parametrize(
        'payload',
        [
            {'fioRepairIntervalDays': 0},
            {'fioRepairIntervalDays': 181},
            {'fioRepairDueSoonDays': -1},
            {'fioRepairDueSoonDays': 61},
            {'fioRepairArrivalDays': -1},
            {'fioRepairArrivalDays': 31},
            {'fioRepairPlanLinks': {'A' * 32: {'planUuid': None}}},
            {'fioRepairPlanLinks': {'a' * 31: {'planUuid': None}}},
            {'fioRepairPlanLinks': {f'{i:032x}': {'planUuid': None} for i in range(201)}},
            {'fioRepairPlanLinks': {'a' * 32: {'planUuid': 'not-a-uuid'}}},
            {'fioRepairPlanLinks': {'a' * 32: {}}},
            {'fioRepairPlanLinks': None},
        ],
    )
    def test_update_rejects_invalid_values(self, api_client, user_factory, payload):
        user = user_factory(id=1)

        assert self._patch(api_client, user, payload).status_code == 400

    def test_update_accepts_200_plan_links(self, api_client, user_factory):
        user = user_factory(id=1)
        links = {f'{i:032x}': {'planUuid': None} for i in range(200)}

        assert self._patch(api_client, user, {'fioRepairPlanLinks': links}).status_code == 200

    def test_plan_links_merge_per_site(self, api_client, user_factory):
        user = user_factory(id=1)
        baker.make(UserPreference, user=user, preferences={'burn_days_red': 3, 'plan_overrides': {'p1': None}})

        self._patch(api_client, user, {'fioRepairPlanLinks': {self.SITE_A: {'planUuid': self.PLAN}}})
        response = self._patch(api_client, user, {'fioRepairPlanLinks': {self.SITE_B: {'planUuid': None}}})

        assert response.status_code == 200
        assert response.data['fioRepairPlanLinks'] == {
            self.SITE_A: {'planUuid': self.PLAN},
            self.SITE_B: {'planUuid': None},
        }

        response = self._patch(api_client, user, {'fioRepairPlanLinks': {self.SITE_A: None}})

        assert response.status_code == 200
        assert response.data['fioRepairPlanLinks'] == {self.SITE_B: {'planUuid': None}}
        stored = UserPreference.objects.get(user=user).preferences
        assert stored['fio_repair_plan_links'] == {self.SITE_B: {'plan_uuid': None}}
        assert (stored['burn_days_red'], stored['plan_overrides']) == (3, {'p1': None})


class TestProfileEmail:
    @staticmethod
    def _patch(api_client, user: User, email: str | None):
        return api_client.as_user(user).patch(reverse('user:user_profile'), data={'email': email}, format='json')

    def test_adding_an_email_stores_it_normalised_and_sends_one_code(
        self, api_client, user_factory, django_capture_on_commit_callbacks
    ):
        user = user_factory(id=1)

        with (
            patch('user.tasks.send_email_verification_code.apply_async') as send,
            django_capture_on_commit_callbacks(execute=True),
        ):
            response = self._patch(api_client, user, ' Pilot@EXAMPLE.com ')

        assert response.status_code == 200
        assert response.data['email'] == 'Pilot@example.com'
        assert response.data['is_email_verified'] is False
        assert (
            VerificationCode.objects.filter(user=user, purpose=VerificationeCodeChoices.EMAIL_VERIFICATION).count() == 1
        )
        send.assert_called_once()

    @pytest.mark.parametrize('cleared', ['', None])
    def test_clearing_the_email(
        self, api_client, user_factory, django_capture_on_commit_callbacks, cleared: str | None
    ):
        user = user_factory(id=1, email='pilot@example.com', is_email_verified=True)
        _make_code(user, VerificationeCodeChoices.PASSWORD_RESET, 'RESET123')

        with (
            patch('user.tasks.send_email_verification_code.apply_async') as send,
            django_capture_on_commit_callbacks(execute=True),
        ):
            response = self._patch(api_client, user, cleared)

        assert response.status_code == 200
        user.refresh_from_db()
        assert (user.email, user.is_email_verified) == (None, False)
        assert not VerificationCode.objects.filter(user=user).exists()
        send.assert_not_called()

    def test_an_address_of_another_account_in_any_case_is_taken(self, api_client, user_factory):
        user_factory(id=1, email='foo@x.com', is_email_verified=True)
        other = user_factory(id=2)

        response = self._patch(api_client, other, 'Foo@X.com')

        assert response.status_code == 400
        assert response.data == {'email': ['email_taken']}
        other.refresh_from_db()
        assert other.email is None

    def test_own_address_is_not_taken(self, api_client, user_factory):
        user = user_factory(id=1, email='foo@x.com', is_email_verified=True)

        response = self._patch(api_client, user, 'foo@x.com')

        assert response.status_code == 200
        user.refresh_from_db()
        assert user.is_email_verified is True

    def test_an_invalid_address_is_a_field_error(self, api_client, user_factory):
        response = self._patch(api_client, user_factory(id=1), 'not-an-address')

        assert response.status_code == 400
        assert 'email' in response.data


class TestSignupEmail:
    PAYLOAD = {
        'username': 'newpilot',
        'password': 'Xk7!qzR9pLm2',
        'planet_id': 'OT-580b',
        'planet_input': 'montem',
    }

    def test_an_address_taken_in_another_case_is_dropped(self, api_client, user_factory):
        user_factory(email='foo@x.com', is_email_verified=True)

        response = api_client.post(
            reverse('user:user_signup'), data={**self.PAYLOAD, 'email': 'FOO@x.com'}, format='json'
        )

        assert response.status_code == 201
        assert User.objects.get(username='newpilot').email is None

    def test_a_rolled_back_signup_sends_nothing(self, api_client, django_capture_on_commit_callbacks):
        with (
            patch('user.tasks.send_email_verification_code.apply_async') as send,
            patch.object(PlanningEmpire.objects, 'create', side_effect=RuntimeError('empire failed')),
            django_capture_on_commit_callbacks(execute=True),
            pytest.raises(RuntimeError),
        ):
            api_client.post(
                reverse('user:user_signup'), data={**self.PAYLOAD, 'email': 'new@example.com'}, format='json'
            )

        send.assert_not_called()
        assert not User.objects.filter(username='newpilot').exists()


class TestEmailNudgePreferences:
    URL = 'user:user_preferences'

    def test_defaults_are_null(self, api_client, user_factory):
        response = api_client.as_user(user_factory(id=1)).get(reverse(self.URL))

        assert response.status_code == 200
        assert (response.data['emailNudgeDismissal'], response.data['emailNudgeDismissedAt']) == (None, None)

    def test_update_reads_back_and_keeps_other_keys(self, api_client, user_factory):
        client = api_client.as_user(user_factory(id=1))
        client.patch(reverse(self.URL), data={'burnDaysRed': 3}, format='json')

        response = client.patch(
            reverse(self.URL),
            data={'emailNudgeDismissal': 'later', 'emailNudgeDismissedAt': '2026-10-16T12:00:00Z'},
            format='json',
        )

        assert response.status_code == 200
        data = client.get(reverse(self.URL)).data
        assert data['emailNudgeDismissal'] == 'later'
        assert data['emailNudgeDismissedAt'] == '2026-10-16T12:00:00Z'
        assert data['burnDaysRed'] == 3

        cleared = client.patch(
            reverse(self.URL), data={'emailNudgeDismissal': None, 'emailNudgeDismissedAt': None}, format='json'
        )
        assert (cleared.data['emailNudgeDismissal'], cleared.data['emailNudgeDismissedAt']) == (None, None)

    @pytest.mark.parametrize('payload', [{'emailNudgeDismissal': 'sometimes'}, {'emailNudgeDismissedAt': 'yesterday'}])
    def test_invalid_values_are_rejected(self, api_client, user_factory, payload: dict[str, str]):
        response = api_client.as_user(user_factory(id=1)).patch(reverse(self.URL), data=payload, format='json')

        assert response.status_code == 400

    def test_an_unknown_stored_dismissal_reads_as_null(self, api_client, user_factory):
        user = user_factory(id=1)
        baker.make('user.UserPreference', user=user, preferences={'email_nudge_dismissal': 'sometimes'})

        response = api_client.as_user(user).get(reverse(self.URL))

        assert response.data['emailNudgeDismissal'] is None


class TestEmailFlowLogs:
    def test_every_flow_logs_its_event_without_address_or_code(
        self, api_client, user_factory, django_capture_on_commit_callbacks
    ):
        email, code = 'pilot@example.com', 'SECRET12'
        user = user_factory(id=1, username='pilot')
        client = api_client.as_user(user)

        def post(name: str, data: dict[str, str] | None = None):
            return client.post(reverse(name), data=data or {}, format='json')

        with (
            capture_logs() as logs,
            patch('user.services.verification_service.VerificationService.generate_numeric_code', return_value=code),
            patch('user.tasks.send_email_verification_code.apply_async'),
            patch('user.tasks.send_password_reset_code.apply_async'),
            django_capture_on_commit_callbacks(execute=True),
        ):
            client.patch(reverse('user:user_profile'), data={'email': email}, format='json')
            post('user:user_request_email_verification')
            post('user:user_verify_email', {'code': 'WRONG123'})
            post('user:user_verify_email', {'code': code})
            post('user:user_request_password_reset', {'email': email.upper()})
            post('user:user_password_reset', {'email': email, 'code': 'WRONG123', 'new_password': 'Xk7!qzR9pLm2'})
            post('user:user_password_reset', {'email': email, 'code': code, 'new_password': 'Xk7!qzR9pLm2'})
            client.patch(reverse('user:user_profile'), data={'email': ''}, format='json')

        events = [
            (e['event'], e.get('reason'), e.get('matched'))
            for e in logs
            if e['event'].startswith(('email', 'password'))
        ]
        assert events == [
            ('email_verification_requested', 'email_change', None),
            ('email_verification_requested', 'resend', None),
            ('email_verify_failed', 'invalid', None),
            ('email_verified', None, None),
            ('password_reset_requested', None, True),
            ('password_reset_failed', 'invalid', None),
            ('password_reset_completed', None, None),
            ('email_removed', None, None),
        ]
        assert all(e.get('user_id') == user.pk for e in logs if e['event'].startswith(('email', 'password')))
        dumped = str(logs).lower()
        assert email not in dumped
        assert code.lower() not in dumped
