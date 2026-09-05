from unittest.mock import patch

import pytest
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
        response = api_client.as_user(user).put(
            url, data=_empire_payload(empire_name='New Name'), format='json'
        )

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

    def test_sync_junctions_creates_and_removes_links(
        self, api_client, user_factory, empire_factory, plan_factory
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

        with patch('planning.api.viewsets.empire_viewset.PlanningCacheManager.delete_pattern') as mock_delete_pattern:
            response = api_client.as_user(user).post(url, data=payload, format='json')

        assert response.status_code == 200
        mock_delete_pattern.assert_called_once_with(f'*PLANNING:{user.id}:*')

        linked_plan_uuids = set(empire.plans.values_list('uuid', flat=True))
        assert linked_plan_uuids == {plan_keep.uuid, plan_new.uuid}

    def test_sync_junctions_no_changes_skips_cache_invalidation(
        self, api_client, user_factory, empire_factory, plan_factory
    ):
        user = user_factory(id=1)
        empire = empire_factory(user=user)
        plan = plan_factory(user=user, plan_data=plan_data_vallis)
        empire.plans.add(plan, through_defaults={'user': user})

        url = reverse('planning:empire-junctions')
        payload = [{'empire_uuid': str(empire.uuid), 'baseplanners': [{'baseplanner_uuid': str(plan.uuid)}]}]

        with patch('planning.api.viewsets.empire_viewset.PlanningCacheManager.delete_pattern') as mock_delete_pattern:
            response = api_client.as_user(user).post(url, data=payload, format='json')

        assert response.status_code == 200
        mock_delete_pattern.assert_not_called()

    def test_sync_junctions_rejects_unowned_references(
        self, api_client, user_factory, empire_factory, plan_factory
    ):
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

    def test_sync_state_updates_empire_state(self, api_client, user_factory, empire_factory, plan_factory):
        user = user_factory(id=1)
        empire = empire_factory(user=user, empire_state={}, needs_state_sync=False)
        plan = plan_factory(user=user, plan_data=plan_data_vallis, planet_natural_id='OT-580b')

        url = reverse('planning:empire-sync-state', kwargs={'pk': str(empire.uuid)})
        payload = {
            'metadata': {
                'faction': PlanningFactionChoices.ANTARES,
                'permits_used': 1,
                'permits_total': 2,
                'plan_count': 1,
                'timestamp': '2026-01-01T00:00:00Z',
            },
            'empire_total': {'H2O': {'p': 10.0, 'c': 5.0, 'd': 5.0}},
            'plan_details': {
                str(plan.uuid): {
                    'metadata': {'planet_natural_id': 'OT-580b', 'cogc': PlanningCOGCChoices.NONE},
                    'deltas': {'H2O': {'p': 10.0, 'c': 5.0, 'd': 5.0}},
                }
            },
        }

        with patch('planning.api.viewsets.empire_viewset.PlanningCacheManager.delete_pattern') as mock_delete_pattern:
            response = api_client.as_user(user).patch(url, data=payload, format='json')

        assert response.status_code == 200
        mock_delete_pattern.assert_called_once_with(f'*PLANNING:{user.id}:*')

        empire.refresh_from_db()
        assert empire.needs_state_sync is True
        assert empire.empire_state['empire_total']['H2O']['p'] == 10.0
