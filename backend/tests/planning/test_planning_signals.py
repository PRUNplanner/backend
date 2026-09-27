"""
Behavioural tests for the planning cache invalidation signals.

They run against a real (in-memory) cache via the `locmem_cache` fixture, so a
stale cache entry shows up as stale data, not as a mocked call.

Tests marked `xfail(strict=True)` document known defects from the performance
audit. They fail today by design; once the defect is fixed they XPASS, which
fails the run so the marker gets removed together with the fix.
"""

from collections.abc import Callable
from typing import Protocol, cast

import pytest
from django.urls import reverse
from planning.models import PlanningCX, PlanningEmpire, PlanningPlan
from rest_framework.test import APIClient
from tests.cache_backends import PatternLocMemCache
from user.models import User

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

    @pytest.mark.xfail(strict=True, reason='audit: plan changes never invalidate the cx list, which nests plans')
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

    @pytest.mark.xfail(strict=True, reason='audit: empire changes never invalidate the cx list, which nests empires')
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

    @pytest.mark.xfail(
        strict=True,
        reason="audit: pattern '*{user_id}:empire:retrieve*' also matches users 11, 21, 101, ... of user 1",
    )
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


class TestInvalidationCost:
    """
    Every `delete_pattern` is a full-keyspace SCAN on Redis (COUNT 10). The number
    of scans per write must not grow with the number of linked rows.
    """

    @pytest.mark.parametrize('linked_plans', [1, 5])
    @pytest.mark.xfail(strict=True, reason='audit: one keyspace scan per cascaded empire-plan junction')
    def test_empire_delete_scans_at_most_once(
        self,
        linked_plans: int,
        user_factory: Callable[..., User],
        empire_factory: Callable[..., PlanningEmpire],
        plan_factory: Callable[..., PlanningPlan],
        django_capture_on_commit_callbacks,
    ):
        user = user_factory()
        empire = empire_factory(user=user)
        for _ in range(linked_plans):
            _link(empire, plan_factory(user=user), user)

        with django_capture_on_commit_callbacks(execute=True):
            empire.delete()

        assert len(PatternLocMemCache.pattern_calls) <= 1

    @pytest.mark.xfail(strict=True, reason='audit: one keyspace scan per cascaded empire-plan junction')
    def test_plan_delete_scans_at_most_once(
        self, user_factory, empire_factory, plan_factory, django_capture_on_commit_callbacks
    ):
        user = user_factory()
        plan = plan_factory(user=user)
        for _ in range(3):
            _link(empire_factory(user=user), plan, user)

        with django_capture_on_commit_callbacks(execute=True):
            plan.delete()

        assert len(PatternLocMemCache.pattern_calls) <= 1

    @pytest.mark.xfail(strict=True, reason='audit: every plan save runs a full keyspace scan')
    def test_plan_save_does_not_scan_keyspace(self, user_factory, plan_factory, django_capture_on_commit_callbacks):
        user = user_factory()
        plan = plan_factory(user=user)

        with django_capture_on_commit_callbacks(execute=True):
            plan.save()

        assert PatternLocMemCache.pattern_calls == []
