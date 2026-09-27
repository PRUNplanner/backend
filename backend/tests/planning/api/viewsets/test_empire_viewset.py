import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from planning.models import PlanningCOGCChoices, PlanningFactionChoices
from tests.fixtures.planning.fxt_plan_vallis import plan_data_vallis

pytestmark = pytest.mark.django_db


def _empire_payload(**overrides):
    payload = {
        'empire_name': 'My Empire',
        'empire_faction': PlanningFactionChoices.ANTARES,
        'empire_permits_used': 1,
        'empire_permits_total': 2,
    }
    payload.update(overrides)
    return payload


class TestEmpireViewSetCrud:
    def test_list_requires_auth(self, api_client, user_factory, empire_factory):
        url = reverse('planning:empire')

        response_noauth = api_client.get(url)
        assert response_noauth.status_code == 401

        user = user_factory(id=1)
        empire_factory(user=user, empire_name='My Empire')

        response = api_client.as_user(user).get(url)
        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]['empire_name'] == 'My Empire'

    def test_retrieve_404_and_200(self, api_client, user_factory, empire_factory):
        user = user_factory(id=1)
        empire = empire_factory(user=user, empire_name='My Empire')

        url_404 = reverse('planning:empire-detail', kwargs={'pk': '356da85a-494a-45a9-b20e-16d0f128c5b8'})
        response_404 = api_client.as_user(user).get(url_404)
        assert response_404.status_code == 404

        url = reverse('planning:empire-detail', kwargs={'pk': str(empire.uuid)})
        response = api_client.as_user(user).get(url)
        assert response.status_code == 200
        assert response.data['uuid'] == str(empire.uuid)

    def test_retrieve_plans(self, api_client, user_factory, empire_factory, plan_factory):
        user = user_factory(id=1)
        empire = empire_factory(user=user)
        plan = plan_factory(user=user, plan_data=plan_data_vallis)
        empire.plans.add(plan, through_defaults={'user': user})

        url = reverse('planning:empire-plan-list', kwargs={'pk': str(empire.uuid)})

        response_noauth = api_client.get(url)
        assert response_noauth.status_code == 401

        response = api_client.as_user(user).get(url)
        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]['uuid'] == str(plan.uuid)

    def test_create(self, api_client, user_factory):
        user = user_factory(id=1)
        url = reverse('planning:empire')

        response_noauth = api_client.post(url, data=_empire_payload(), format='json')
        assert response_noauth.status_code == 401

        response = api_client.as_user(user).post(url, data=_empire_payload(), format='json')
        assert response.status_code == 201
        assert response.data['empire_name'] == 'My Empire'

    def test_update(self, api_client, user_factory, empire_factory):
        user = user_factory(id=1)
        empire = empire_factory(user=user, empire_name='Old Name')

        url = reverse('planning:empire-detail', kwargs={'pk': str(empire.uuid)})
        response = api_client.as_user(user).put(url, data=_empire_payload(empire_name='New Name'), format='json')

        assert response.status_code == 200
        assert response.data['empire_name'] == 'New Name'

    def test_destroy(self, api_client, user_factory, empire_factory):
        user = user_factory(id=1)
        empire = empire_factory(user=user)

        url = reverse('planning:empire-detail', kwargs={'pk': str(empire.uuid)})

        response_noauth = api_client.delete(url)
        assert response_noauth.status_code == 401

        response = api_client.as_user(user).delete(url)
        assert response.status_code == 204


class TestEmpireViewSetSyncJunctions:
    def test_sync_junctions_requires_auth(self, api_client):
        url = reverse('planning:empire-junctions')

        response = api_client.post(url, data=[], format='json')
        assert response.status_code == 401

    @pytest.mark.usefixtures('locmem_cache')
    def test_sync_junctions_creates_and_removes_links(
        self, api_client, user_factory, empire_factory, plan_factory, django_capture_on_commit_callbacks
    ):
        user = user_factory(id=1)
        empire = empire_factory(user=user)
        plan_keep = plan_factory(user=user, plan_data=plan_data_vallis)
        plan_new = plan_factory(user=user, plan_data=plan_data_vallis)
        plan_drop = plan_factory(user=user, plan_data=plan_data_vallis)

        empire.plans.add(plan_keep, through_defaults={'user': user})
        empire.plans.add(plan_drop, through_defaults={'user': user})

        url = reverse('planning:empire-junctions')
        payload = [
            {
                'empire_uuid': str(empire.uuid),
                'baseplanners': [
                    {'baseplanner_uuid': str(plan_keep.uuid)},
                    {'baseplanner_uuid': str(plan_new.uuid)},
                ],
            }
        ]

        plan_list_url = reverse('planning:plan')
        api_client.as_user(user).get(plan_list_url)
        assert api_client.as_user(user).get(plan_list_url)['X-Cache-Hit'] == '1'

        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.as_user(user).post(url, data=payload, format='json')

        assert response.status_code == 200
        assert {p['uuid'] for p in response.data[0]['plans']} == {str(plan_keep.uuid), str(plan_new.uuid)}

        linked_plan_uuids = set(empire.plans.values_list('uuid', flat=True))
        assert linked_plan_uuids == {plan_keep.uuid, plan_new.uuid}

        # the cached plan list nests empires, so it must reflect the new links
        plans = {p['uuid']: p for p in api_client.as_user(user).get(plan_list_url).data}
        assert [e['uuid'] for e in plans[str(plan_new.uuid)]['empires']] == [str(empire.uuid)]
        assert plans[str(plan_drop.uuid)]['empires'] == []

    @pytest.mark.usefixtures('locmem_cache')
    def test_sync_junctions_no_changes_keeps_caches(self, api_client, user_factory, empire_factory, plan_factory):
        user = user_factory(id=1)
        empire = empire_factory(user=user)
        plan = plan_factory(user=user, plan_data=plan_data_vallis)
        empire.plans.add(plan, through_defaults={'user': user})

        plan_list_url = reverse('planning:plan')
        api_client.as_user(user).get(plan_list_url)

        url = reverse('planning:empire-junctions')
        payload = [{'empire_uuid': str(empire.uuid), 'baseplanners': [{'baseplanner_uuid': str(plan.uuid)}]}]

        response = api_client.as_user(user).post(url, data=payload, format='json')

        assert response.status_code == 200
        assert api_client.as_user(user).get(plan_list_url)['X-Cache-Hit'] == '1'

    def test_sync_junctions_rejects_unowned_references(self, api_client, user_factory, empire_factory, plan_factory):
        user = user_factory(id=1)
        other_user = user_factory(id=2)
        empire = empire_factory(user=user)
        other_plan = plan_factory(user=other_user, plan_data=plan_data_vallis)

        url = reverse('planning:empire-junctions')
        payload = [{'empire_uuid': str(empire.uuid), 'baseplanners': [{'baseplanner_uuid': str(other_plan.uuid)}]}]

        response = api_client.as_user(user).post(url, data=payload, format='json')

        assert response.status_code == 403
        assert response.data['invalid_plans'] == [other_plan.uuid]


class TestEmpireViewSetSyncState:
    def test_sync_state_requires_auth(self, api_client, user_factory, empire_factory):
        user = user_factory(id=1)
        empire = empire_factory(user=user)

        url = reverse('planning:empire-sync-state', kwargs={'pk': str(empire.uuid)})
        response = api_client.patch(url, data={}, format='json')

        assert response.status_code == 401

    @pytest.mark.usefixtures('locmem_cache')
    def test_sync_state_updates_empire_state(self, api_client, user_factory, empire_factory, plan_factory):
        user = user_factory(id=1)
        empire = empire_factory(user=user, empire_state={}, needs_state_sync=False)
        plan = plan_factory(user=user, plan_data=plan_data_vallis, planet_natural_id='OT-580b')

        url = reverse('planning:empire-sync-state', kwargs={'pk': str(empire.uuid)})
        response = api_client.as_user(user).patch(url, data=_state_payload(str(plan.uuid)), format='json')

        assert response.status_code == 200

        empire.refresh_from_db()
        assert empire.needs_state_sync is True
        assert empire.empire_state['empire_total']['H2O']['p'] == 10.0

    @pytest.mark.usefixtures('locmem_cache')
    def test_sync_state_keeps_planning_caches(
        self, api_client, user_factory, empire_factory, plan_factory, django_capture_on_commit_callbacks
    ):
        user = user_factory(id=1)
        empire = empire_factory(user=user)
        plan = plan_factory(user=user, plan_data=plan_data_vallis, planet_natural_id='OT-580b')

        list_url = reverse('planning:empire')
        api_client.as_user(user).get(list_url)

        url = reverse('planning:empire-sync-state', kwargs={'pk': str(empire.uuid)})
        with django_capture_on_commit_callbacks(execute=True):
            response = api_client.as_user(user).patch(url, data=_state_payload(str(plan.uuid)), format='json')

        assert response.status_code == 200
        assert api_client.as_user(user).get(list_url)['X-Cache-Hit'] == '1'


class TestEmpireViewSetQueries:
    def test_list_query_count_is_constant(
        self, api_client, user_factory, empire_factory, plan_factory, cx_factory, django_assert_num_queries
    ):
        user = user_factory(id=1)
        for _ in range(5):
            empire = empire_factory(user=user, cx=cx_factory(user=user))
            empire.plans.add(plan_factory(user=user), through_defaults={'user': user})

        # empires, plans prefetch, cx prefetch
        with django_assert_num_queries(3):
            response = api_client.as_user(user).get(reverse('planning:empire'))

        assert len(response.data) == 5

    @pytest.mark.xfail(strict=True, reason='audit: empire_state is loaded for every empire but never serialized')
    def test_list_does_not_load_empire_state(self, api_client, user_factory, empire_factory):
        user = user_factory(id=1)
        empire_factory(user=user)

        with CaptureQueriesContext(connection) as ctx:
            api_client.as_user(user).get(reverse('planning:empire'))

        assert not any('empire_state' in q['sql'] for q in ctx.captured_queries)


def _state_payload(plan_uuid: str) -> dict[str, object]:
    return {
        'metadata': {
            'faction': PlanningFactionChoices.ANTARES,
            'permits_used': 1,
            'permits_total': 2,
            'plan_count': 1,
            'timestamp': '2026-01-01T00:00:00Z',
        },
        'empire_total': {'H2O': {'p': 10.0, 'c': 5.0, 'd': 5.0}},
        'plan_details': {
            plan_uuid: {
                'metadata': {'planet_natural_id': 'OT-580b', 'cogc': PlanningCOGCChoices.NONE},
                'deltas': {'H2O': {'p': 10.0, 'c': 5.0, 'd': 5.0}},
            }
        },
    }
