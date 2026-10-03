"""
Behavioural tests for the planning cache invalidation signals.

They run against a real (in-memory) cache via the `locmem_cache` fixture, so a
stale cache entry shows up as stale data, not as a mocked call.

Tests marked `xfail(strict=True)` document known defects from the performance
audit. They fail today by design; once the defect is fixed they XPASS, which
fails the run so the marker gets removed together with the fix.
"""

from typing import Protocol, cast

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from planning.models import PlanningCX, PlanningEmpire, PlanningPlan
from rest_framework.test import APIClient
from user.models import User, UserPreference

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('locmem_cache')]


class _ListResponse(Protocol):
    """A test response with its decoded JSON body, see the `api_client` fixture."""

    data: list

    def __getitem__(self, header: str) -> str: ...


class _DetailResponse(Protocol):
    data: dict

    def __getitem__(self, header: str) -> str: ...


def _get(api_client: APIClient, user: User, url: str) -> _ListResponse:
    return cast(_ListResponse, api_client.as_user(user).get(url))  # ty:ignore[unresolved-attribute]


def _get_detail(api_client: APIClient, user: User, url: str) -> _DetailResponse:
    return cast(_DetailResponse, api_client.as_user(user).get(url))  # ty:ignore[unresolved-attribute]


def _prime(api_client: APIClient, user: User, url: str) -> None:
    """Fills the cache for `url` and asserts the second read is served from it."""
    assert _get(api_client, user, url)['X-Cache-Hit'] == '0'
    assert _get(api_client, user, url)['X-Cache-Hit'] == '1'


def _link(empire: PlanningEmpire, plan: PlanningPlan, user: User) -> None:
    empire.plans.add(plan, through_defaults={'user': user})


class TestPlanChangesInvalidate:
    def test_plan_rename_refreshes_plan_list(
        self, api_client, user_factory, plan_factory, django_capture_on_commit_callbacks
    ):
        user = user_factory()
        plan = plan_factory(user=user, plan_name='Before')
        url = reverse('planning:plan')
        _prime(api_client, user, url)

        with django_capture_on_commit_callbacks(execute=True):
            plan.plan_name = 'After'
            plan.save()

        response = _get(api_client, user, url)
        assert response['X-Cache-Hit'] == '0'
        assert response.data[0]['plan_name'] == 'After'

    def test_plan_rename_refreshes_plan_detail(
        self, api_client, user_factory, plan_factory, django_capture_on_commit_callbacks
    ):
        user = user_factory()
        plan = plan_factory(user=user, plan_name='Before')
        url = reverse('planning:plan-detail', kwargs={'pk': str(plan.uuid)})
        _prime(api_client, user, url)

        with django_capture_on_commit_callbacks(execute=True):
            plan.plan_name = 'After'
            plan.save()

        assert _get_detail(api_client, user, url).data['plan_name'] == 'After'

    def test_plan_delete_refreshes_plan_list(
        self, api_client, user_factory, plan_factory, django_capture_on_commit_callbacks
    ):
        user = user_factory()
        plan = plan_factory(user=user)
        url = reverse('planning:plan')
        _prime(api_client, user, url)

        with django_capture_on_commit_callbacks(execute=True):
            plan.delete()

        assert _get(api_client, user, url).data == []

    def test_plan_rename_refreshes_nested_plans_in_empire_views(
        self, api_client, user_factory, plan_factory, empire_factory, django_capture_on_commit_callbacks
    ):
        user = user_factory()
        empire = empire_factory(user=user)
        plan = plan_factory(user=user, plan_name='Before')
        _link(empire, plan, user)

        list_url = reverse('planning:empire')
        detail_url = reverse('planning:empire-detail', kwargs={'pk': str(empire.uuid)})
        plans_url = reverse('planning:empire-plan-list', kwargs={'pk': str(empire.uuid)})
        for url in (list_url, detail_url, plans_url):
            _prime(api_client, user, url)

        with django_capture_on_commit_callbacks(execute=True):
            plan.plan_name = 'After'
            plan.save()

        assert _get(api_client, user, list_url).data[0]['plans'][0]['plan_name'] == 'After'
        assert _get_detail(api_client, user, detail_url).data['plans'][0]['plan_name'] == 'After'
        assert _get(api_client, user, plans_url).data[0]['plan_name'] == 'After'

    def test_plan_rename_refreshes_nested_plans_in_cx_list(
        self, api_client, user_factory, plan_factory, empire_factory, cx_factory, django_capture_on_commit_callbacks
    ):
        user = user_factory()
        cx = cx_factory(user=user)
        empire = empire_factory(user=user, cx=cx)
        plan = plan_factory(user=user, plan_name='Before')
        _link(empire, plan, user)
        url = reverse('planning:cx')
        _prime(api_client, user, url)

        with django_capture_on_commit_callbacks(execute=True):
            plan.plan_name = 'After'
            plan.save()

        assert _get(api_client, user, url).data[0]['empires'][0]['plans'][0]['plan_name'] == 'After'


class TestEmpireChangesInvalidate:
    def test_empire_rename_refreshes_empire_views(
        self, api_client, user_factory, empire_factory, django_capture_on_commit_callbacks
    ):
        user = user_factory()
        empire = empire_factory(user=user, empire_name='Before')
        list_url = reverse('planning:empire')
        detail_url = reverse('planning:empire-detail', kwargs={'pk': str(empire.uuid)})
        _prime(api_client, user, list_url)
        _prime(api_client, user, detail_url)

        with django_capture_on_commit_callbacks(execute=True):
            empire.empire_name = 'After'
            empire.save()

        assert _get(api_client, user, list_url).data[0]['empire_name'] == 'After'
        assert _get_detail(api_client, user, detail_url).data['empire_name'] == 'After'

    def test_empire_rename_refreshes_nested_empires_in_plan_list(
        self, api_client, user_factory, plan_factory, empire_factory, django_capture_on_commit_callbacks
    ):
        user = user_factory()
        empire = empire_factory(user=user, empire_name='Before')
        _link(empire, plan_factory(user=user), user)
        url = reverse('planning:plan')
        _prime(api_client, user, url)

        with django_capture_on_commit_callbacks(execute=True):
            empire.empire_name = 'After'
            empire.save()

        assert _get(api_client, user, url).data[0]['empires'][0]['empire_name'] == 'After'

    def test_empire_rename_refreshes_nested_empires_in_cx_list(
        self, api_client, user_factory, empire_factory, cx_factory, django_capture_on_commit_callbacks
    ):
        user = user_factory()
        cx = cx_factory(user=user)
        empire = empire_factory(user=user, cx=cx, empire_name='Before')
        url = reverse('planning:cx')
        _prime(api_client, user, url)

        with django_capture_on_commit_callbacks(execute=True):
            empire.empire_name = 'After'
            empire.save()

        assert _get(api_client, user, url).data[0]['empires'][0]['empire_name'] == 'After'


class TestCXChangesInvalidate:
    def test_cx_rename_refreshes_every_view_nesting_it(
        self, api_client, user_factory, plan_factory, empire_factory, cx_factory, django_capture_on_commit_callbacks
    ):
        user = user_factory()
        cx: PlanningCX = cx_factory(user=user, cx_name='Before')
        empire = empire_factory(user=user, cx=cx)
        _link(empire, plan_factory(user=user), user)

        cx_url = reverse('planning:cx')
        empire_url = reverse('planning:empire')
        plan_url = reverse('planning:plan')
        for url in (cx_url, empire_url, plan_url):
            _prime(api_client, user, url)

        with django_capture_on_commit_callbacks(execute=True):
            cx.cx_name = 'After'
            cx.save()

        assert _get(api_client, user, cx_url).data[0]['cx_name'] == 'After'
        assert _get(api_client, user, empire_url).data[0]['cx']['cx_name'] == 'After'
        assert _get(api_client, user, plan_url).data[0]['empires'][0]['cx']['cx_name'] == 'After'


class TestInvalidationScope:
    def test_changes_leave_other_users_caches_alone(
        self, api_client, user_factory, plan_factory, django_capture_on_commit_callbacks
    ):
        user = user_factory(id=2)
        other = user_factory(id=3)
        plan = plan_factory(user=user)
        plan_factory(user=other)
        url = reverse('planning:plan')
        _prime(api_client, other, url)

        with django_capture_on_commit_callbacks(execute=True):
            plan.save()

        assert _get(api_client, other, url)['X-Cache-Hit'] == '1'

    def test_changes_leave_caches_of_users_with_suffix_ids_alone(
        self, api_client, user_factory, empire_factory, plan_factory, django_capture_on_commit_callbacks
    ):
        user = user_factory(id=1)
        other = user_factory(id=11)
        plan = plan_factory(user=user)
        other_empire = empire_factory(user=other)
        url = reverse('planning:empire-detail', kwargs={'pk': str(other_empire.uuid)})
        _prime(api_client, other, url)

        with django_capture_on_commit_callbacks(execute=True):
            plan.save()

        assert _get(api_client, other, url)['X-Cache-Hit'] == '1'


class TestPlanDeleteDropsOverrides:
    OVERRIDE = {'include_cm': True, 'visitation_material_exclusions': [], 'auto_optimize_habs': False}

    def test_delete_removes_only_that_plans_override(self, api_client, user_factory, plan_factory) -> None:
        user, other = user_factory(), user_factory()
        plan, kept = plan_factory(user=user), plan_factory(user=user)
        other_plan = plan_factory(user=other)
        UserPreference.objects.create(
            user=user,
            preferences={
                'burn_days_red': 3,
                'plan_overrides': {str(plan.uuid): self.OVERRIDE, str(kept.uuid): self.OVERRIDE},
            },
        )
        UserPreference.objects.create(user=other, preferences={'plan_overrides': {str(plan.uuid): self.OVERRIDE}})

        response = api_client.as_user(user).delete(reverse('planning:plan-detail', kwargs={'pk': str(plan.uuid)}))

        assert response.status_code == 204
        assert UserPreference.objects.get(user=user).preferences == {
            'burn_days_red': 3,
            'plan_overrides': {str(kept.uuid): self.OVERRIDE},
        }
        assert UserPreference.objects.get(user=other).preferences == {'plan_overrides': {str(plan.uuid): self.OVERRIDE}}
        assert PlanningPlan.objects.filter(pk=other_plan.pk).exists()

    def test_delete_without_preferences_or_override(self, user_factory, plan_factory) -> None:
        user = user_factory()
        plan_factory(user=user).delete()
        UserPreference.objects.create(user=user, preferences={'plan_overrides': None})

        plan_factory(user=user).delete()

        assert UserPreference.objects.get(user=user).preferences == {'plan_overrides': None}

    def test_account_deletion_skips_override_cleanup(self, user_factory, plan_factory) -> None:
        user = user_factory()
        plans = [plan_factory(user=user) for _ in range(5)]
        UserPreference.objects.create(
            user=user, preferences={'plan_overrides': {str(p.uuid): self.OVERRIDE for p in plans}}
        )
        table = UserPreference._meta.db_table

        with CaptureQueriesContext(connection) as ctx:
            user.delete()

        # no per-plan preference lookup: the cascade deletes the preferences anyway
        assert not [q for q in ctx.captured_queries if q['sql'].startswith('SELECT') and table in q['sql']]
        assert not UserPreference.objects.filter(user_id=user.pk).exists()
