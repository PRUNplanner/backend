from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from planning.models import PlanningEmpirePlan, PlanningPlan
from tests.fixtures.planning.fxt_plan_summary import plan_summary
from tests.fixtures.planning.fxt_plan_vallis import plan_data_vallis

pytestmark = pytest.mark.django_db


def _plan_payload(**overrides):
    payload = {
        'plan_name': 'My Plan',
        'planet_natural_id': 'OT-580b',
        'plan_permits_used': 1,
        'plan_corphq': False,
        'plan_data': plan_data_vallis,
    }
    payload.update(overrides)
    return payload


class TestPlanViewSetCrud:
    def test_list_requires_auth(self, api_client, user_factory, plan_factory):
        url = reverse('planning:plan')

        response_noauth = api_client.get(url)
        assert response_noauth.status_code == 401

        user = user_factory(id=1)
        plan_factory(user=user, plan_data=plan_data_vallis, plan_name='My Plan')

        response = api_client.as_user(user).get(url)
        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]['plan_name'] == 'My Plan'

    def test_retrieve_404_and_200(self, api_client, user_factory, plan_factory):
        user = user_factory(id=1)
        plan = plan_factory(user=user, plan_data=plan_data_vallis)

        url_404 = reverse('planning:plan-detail', kwargs={'pk': '356da85a-494a-45a9-b20e-16d0f128c5b8'})
        response_404 = api_client.as_user(user).get(url_404)
        assert response_404.status_code == 404

        url = reverse('planning:plan-detail', kwargs={'pk': str(plan.uuid)})
        response = api_client.as_user(user).get(url)
        assert response.status_code == 200
        assert response.data['uuid'] == str(plan.uuid)

    def test_retrieve_only_returns_own_plans(self, api_client, user_factory, plan_factory):
        user = user_factory(id=1)
        other_user = user_factory(id=2)
        other_plan = plan_factory(user=other_user, plan_data=plan_data_vallis)

        url = reverse('planning:plan-detail', kwargs={'pk': str(other_plan.uuid)})
        response = api_client.as_user(user).get(url)

        assert response.status_code == 404

    def test_create(self, api_client, user_factory):
        user = user_factory(id=1)
        url = reverse('planning:plan')

        response_noauth = api_client.post(url, data=_plan_payload(), format='json')
        assert response_noauth.status_code == 401

        response = api_client.as_user(user).post(url, data=_plan_payload(), format='json')
        assert response.status_code == 201
        assert response.data['plan_name'] == 'My Plan'

    @pytest.mark.parametrize(
        'plan_data',
        [
            {**plan_data_vallis, 'buildings': [{'name': 'C', 'amount': 1, 'active_recipes': []}]},
            {**plan_data_vallis, 'infrastructure': [{'building': 'XYZ', 'amount': 1}]},
            # used to reach Postgres as a 500 (int64 overflow)
            {**plan_data_vallis, 'experts': [{'type': 'Agriculture', 'amount': 10**20}]},
            {**plan_data_vallis, 'buildings': [{'name': 'FP', 'amount': 100_001, 'active_recipes': []}]},
        ],
    )
    def test_create_rejects_invalid_plan_data(self, api_client, user_factory, plan_data):
        user = user_factory(id=1)

        response = api_client.as_user(user).post(
            reverse('planning:plan'), data=_plan_payload(plan_data=plan_data), format='json'
        )

        assert response.status_code == 400

    def test_create_links_empire_when_empire_uuid_provided(self, api_client, user_factory, empire_factory):
        user = user_factory(id=1)
        empire = empire_factory(user=user)

        url = reverse('planning:plan')
        response = api_client.as_user(user).post(url, data=_plan_payload(empire_uuid=str(empire.uuid)), format='json')

        assert response.status_code == 201
        empire.refresh_from_db()
        assert empire.plans.count() == 1

    def test_update(self, api_client, user_factory, plan_factory):
        user = user_factory(id=1)
        plan = plan_factory(user=user, plan_data=plan_data_vallis, plan_name='Old Name')

        url = reverse('planning:plan-detail', kwargs={'pk': str(plan.uuid)})
        response = api_client.as_user(user).put(url, data=_plan_payload(plan_name='New Name'), format='json')

        assert response.status_code == 200
        assert response.data['plan_name'] == 'New Name'

    @pytest.mark.parametrize(('amount', 'status'), [(100_000, 200), (100_001, 400), (10**20, 400)])
    def test_update_caps_amounts(self, api_client, user_factory, plan_factory, amount: int, status: int):
        user = user_factory(id=1)
        plan = plan_factory(user=user, plan_data=plan_data_vallis)
        plan_data = {**plan_data_vallis, 'buildings': [{'name': 'FP', 'amount': amount, 'active_recipes': []}]}

        url = reverse('planning:plan-detail', kwargs={'pk': str(plan.uuid)})
        response = api_client.as_user(user).put(url, data=_plan_payload(plan_data=plan_data), format='json')

        assert response.status_code == status

    def test_destroy(self, api_client, user_factory, plan_factory):
        user = user_factory(id=1)
        plan = plan_factory(user=user, plan_data=plan_data_vallis)

        url = reverse('planning:plan-detail', kwargs={'pk': str(plan.uuid)})

        response_noauth = api_client.delete(url)
        assert response_noauth.status_code == 401

        response = api_client.as_user(user).delete(url)
        assert response.status_code == 204


class TestPlanViewSetQueryCount:
    def test_list_does_not_n_plus_one_on_empire_cx(
        self, api_client, user_factory, plan_factory, empire_factory, cx_factory, django_assert_max_num_queries
    ):
        user = user_factory(id=1)
        plan = plan_factory(user=user, plan_data=plan_data_vallis)

        # multiple empires, each with a distinct cx, linked to the same plan
        for _ in range(3):
            cx = cx_factory(user=user)
            empire = empire_factory(user=user, cx=cx)
            PlanningEmpirePlan.objects.create(user=user, empire=empire, plan=plan)

        url = reverse('planning:plan')

        # 1 query for plans, 1 for empires JOIN cx, 1 for the m2m-through rows
        with django_assert_max_num_queries(3):
            response = api_client.as_user(user).get(url)

        assert response.status_code == 200
        assert len(response.data[0]['empires']) == 3
        assert {e['cx']['uuid'] for e in response.data[0]['empires']} == {str(e.cx.uuid) for e in plan.empires.all()}


class TestPlanViewSetClone:
    def test_clone_requires_auth(self, api_client, user_factory, plan_factory):
        user = user_factory(id=1)
        plan = plan_factory(user=user, plan_data=plan_data_vallis)

        url = reverse('planning:plan-clone', kwargs={'pk': str(plan.uuid)})
        response = api_client.post(url)

        assert response.status_code == 401

    def test_clone_creates_new_plan_with_suffixed_name(self, api_client, user_factory, plan_factory):
        user = user_factory(id=1)
        plan = plan_factory(user=user, plan_data=plan_data_vallis, plan_name='Original')

        url = reverse('planning:plan-clone', kwargs={'pk': str(plan.uuid)})
        response = api_client.as_user(user).post(url)

        assert response.status_code == 201
        assert response.data['plan_name'] == 'Original (Clone)'
        assert response.data['uuid'] != str(plan.uuid)
        assert PlanningPlan.objects.filter(user=user).count() == 2

    def test_clone_keeps_long_names_within_limit(self, api_client, user_factory, plan_factory):
        user = user_factory(id=1)
        plan = plan_factory(user=user, plan_data=plan_data_vallis, plan_name='x' * 200)

        response = api_client.as_user(user).post(reverse('planning:plan-clone', kwargs={'pk': str(plan.uuid)}))

        assert response.status_code == 201
        assert response.data['plan_name'] == f'{"x" * 192} (Clone)'

    def test_clone_response_is_the_clone(self, api_client, user_factory, plan_factory, empire_factory):
        """READ-02: the response showed the source's empires; the clone has none, as its GET says."""
        user = user_factory(id=1)
        plan = plan_factory(user=user, plan_data=plan_data_vallis)
        empire_factory(user=user).plans.add(plan, through_defaults={'user': user})

        response = api_client.as_user(user).post(reverse('planning:plan-clone', kwargs={'pk': str(plan.uuid)}))
        detail = api_client.as_user(user).get(reverse('planning:plan-detail', kwargs={'pk': response.data['uuid']}))

        assert response.status_code == 201
        assert response.data['empires'] == []
        assert response.data == detail.data


class TestPlanViewSetQueries:
    @staticmethod
    def _put(api_client, user, plan_factory, empire_factory, cx_factory, empire_count: int) -> list[str]:
        plan = plan_factory(user=user, plan_data=plan_data_vallis)
        for _ in range(empire_count):
            empire_factory(user=user, cx=cx_factory(user=user)).plans.add(plan, through_defaults={'user': user})
        url = reverse('planning:plan-detail', kwargs={'pk': str(plan.uuid)})

        with CaptureQueriesContext(connection) as ctx:
            response = api_client.as_user(user).put(url, data=_plan_payload(), format='json')

        assert response.status_code == 200
        assert len(response.data['empires']) == empire_count
        return [q['sql'] for q in ctx.captured_queries]

    def test_update_reuses_the_empires_prefetch(
        self, api_client, user_factory, plan_factory, empire_factory, cx_factory
    ):
        """PERF-04: the response re-read empires with empire_state and one CX query per empire."""
        user = user_factory(id=1)

        one = self._put(api_client, user, plan_factory, empire_factory, cx_factory, 1)
        three = self._put(api_client, user, plan_factory, empire_factory, cx_factory, 3)

        assert len(three) == len(one)
        assert not any('empire_state' in sql for sql in three)

    def test_list_query_count_is_constant(
        self, api_client, user_factory, plan_factory, empire_factory, cx_factory, django_assert_num_queries
    ):
        user = user_factory(id=1)
        for _ in range(5):
            empire = empire_factory(user=user, cx=cx_factory(user=user))
            empire.plans.add(plan_factory(user=user), through_defaults={'user': user})

        # plans, empires prefetch with joined cx
        with django_assert_num_queries(2):
            response = api_client.as_user(user).get(reverse('planning:plan'))

        assert len(response.data) == 5

    def test_list_does_not_load_empire_state(self, api_client, user_factory, plan_factory, empire_factory):
        user = user_factory(id=1)
        empire = empire_factory(user=user)
        empire.plans.add(plan_factory(user=user), through_defaults={'user': user})

        with CaptureQueriesContext(connection) as ctx:
            api_client.as_user(user).get(reverse('planning:plan'))

        assert not any('empire_state' in q['sql'] for q in ctx.captured_queries)


class TestPlanViewSetCacheHeaders:
    def test_list_response_is_private(self, api_client, user_factory, plan_factory):
        user = user_factory(id=1)
        plan_factory(user=user)

        response = api_client.as_user(user).get(reverse('planning:plan'))

        assert 'private' in response['Cache-Control']
        assert 'public' not in response['Cache-Control']


class TestPlanSummary:
    def test_save_stores_summary(self, api_client, user_factory, plan_factory):
        user = user_factory()
        plan = plan_factory(user=user, plan_data=plan_data_vallis)
        url = reverse('planning:plan-detail', kwargs={'pk': str(plan.uuid)})

        response = api_client.as_user(user).put(url, data=_plan_payload(plan_summary=plan_summary), format='json')

        assert response.status_code == 200
        assert response.data['plan_summary']['profit'] == plan_summary['profit']
        assert response.data['plan_summary_at'] is not None
        plan.refresh_from_db()
        assert plan.plan_summary == plan_summary
        assert plan.plan_summary_at is not None

    def test_create_stores_summary(self, api_client, user_factory):
        user = user_factory()
        response = api_client.as_user(user).post(
            reverse('planning:plan'), data=_plan_payload(plan_summary=plan_summary), format='json'
        )

        assert response.status_code == 201
        assert PlanningPlan.objects.get(uuid=response.data['uuid']).plan_summary == plan_summary

    def test_save_without_summary_keeps_it(self, api_client, user_factory, plan_factory):
        user = user_factory()
        stamp = timezone.now() - timedelta(days=3)
        plan = plan_factory(user=user, plan_data=plan_data_vallis, plan_summary=plan_summary, plan_summary_at=stamp)
        url = reverse('planning:plan-detail', kwargs={'pk': str(plan.uuid)})

        response = api_client.as_user(user).put(url, data=_plan_payload(plan_name='Renamed'), format='json')

        assert response.status_code == 200
        plan.refresh_from_db()
        assert plan.plan_summary == plan_summary
        assert plan.plan_summary_at == stamp

    @pytest.mark.parametrize('summary', [None, {'v': 1}, {**plan_summary, 'profit': 'lots'}])
    def test_invalid_summary_is_rejected(self, api_client, user_factory, plan_factory, summary):
        user = user_factory()
        plan = plan_factory(user=user, plan_data=plan_data_vallis)
        url = reverse('planning:plan-detail', kwargs={'pk': str(plan.uuid)})

        response = api_client.as_user(user).put(url, data=_plan_payload(plan_summary=summary), format='json')

        assert response.status_code == 400
        assert 'plan_summary' in response.data
