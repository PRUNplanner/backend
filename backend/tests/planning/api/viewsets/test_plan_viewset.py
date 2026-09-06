import pytest
from django.urls import reverse
from planning.models import PlanningEmpirePlan, PlanningPlan
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
