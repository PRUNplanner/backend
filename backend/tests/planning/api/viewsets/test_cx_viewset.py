from unittest.mock import patch

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


class TestCXViewSetCrud:
    def test_list_requires_auth(self, api_client, user_factory, cx_factory):
        url = reverse('planning:cx')

        response_noauth = api_client.get(url)
        assert response_noauth.status_code == 401

        user = user_factory(id=1)
        cx_factory(user=user, cx_name='My CX')

        response = api_client.as_user(user).get(url)
        assert response.status_code == 200
        assert len(response.data) == 1
        assert response.data[0]['cx_name'] == 'My CX'

    def test_retrieve_404_and_200(self, api_client, user_factory, cx_factory):
        user = user_factory(id=1)
        cx = cx_factory(user=user, cx_name='My CX')

        url_404 = reverse('planning:cx-detail', kwargs={'pk': '356da85a-494a-45a9-b20e-16d0f128c5b8'})
        response_404 = api_client.as_user(user).get(url_404)
        assert response_404.status_code == 404

        url = reverse('planning:cx-detail', kwargs={'pk': str(cx.uuid)})
        response = api_client.as_user(user).get(url)
        assert response.status_code == 200
        assert response.data['uuid'] == str(cx.uuid)

    def test_create(self, api_client, user_factory):
        user = user_factory(id=1)
        url = reverse('planning:cx')
        post_data = {'cx_name': 'New CX', 'cx_data': {}}

        response_noauth = api_client.post(url, data=post_data, format='json')
        assert response_noauth.status_code == 401

        response = api_client.as_user(user).post(url, data=post_data, format='json')
        assert response.status_code == 201
        assert response.data['cx_name'] == 'New CX'

    def test_update(self, api_client, user_factory, cx_factory):
        user = user_factory(id=1)
        cx = cx_factory(user=user, cx_name='Old Name', cx_data={})

        url = reverse('planning:cx-detail', kwargs={'pk': str(cx.uuid)})
        response = api_client.as_user(user).put(url, data={'cx_name': 'Updated Name', 'cx_data': {}}, format='json')

        assert response.status_code == 200
        assert response.data['cx_name'] == 'Updated Name'

    def test_destroy(self, api_client, user_factory, cx_factory):
        user = user_factory(id=1)
        cx = cx_factory(user=user, cx_name='To Delete')

        url = reverse('planning:cx-detail', kwargs={'pk': str(cx.uuid)})

        response_noauth = api_client.delete(url)
        assert response_noauth.status_code == 401

        response = api_client.as_user(user).delete(url)
        assert response.status_code == 204


class TestCXViewSetSyncJunctions:
    def test_sync_junctions_requires_auth(self, api_client):
        url = reverse('planning:cx-junctions')

        response = api_client.post(url, data=[], format='json')
        assert response.status_code == 401

    def test_sync_junctions_assigns_empire_to_cx(self, api_client, user_factory, cx_factory, empire_factory):
        user = user_factory(id=1)
        cx = cx_factory(user=user, cx_name='My CX')
        empire = empire_factory(user=user)

        url = reverse('planning:cx-junctions')
        payload = [{'cx_uuid': str(cx.uuid), 'empires': [{'empire_uuid': str(empire.uuid)}]}]

        with patch('planning.api.viewsets.cx_viewset.PlanningCacheManager.delete_pattern') as mock_delete_pattern:
            response = api_client.as_user(user).post(url, data=payload, format='json')

        assert response.status_code == 200
        mock_delete_pattern.assert_called_once_with(f'*PLANNING:{user.id}:*')

        empire.refresh_from_db()
        assert empire.cx_id == cx.uuid

    def test_sync_junctions_rejects_unowned_cx(self, api_client, user_factory, cx_factory, empire_factory):
        user = user_factory(id=1)
        other_user = user_factory(id=2)
        other_cx = cx_factory(user=other_user)
        empire = empire_factory(user=user)

        url = reverse('planning:cx-junctions')
        payload = [{'cx_uuid': str(other_cx.uuid), 'empires': [{'empire_uuid': str(empire.uuid)}]}]

        response = api_client.as_user(user).post(url, data=payload, format='json')

        assert response.status_code == 403
        assert response.data['error'] == 'Invalid CX UUIDs detected.'

    def test_sync_junctions_rejects_unowned_empire(self, api_client, user_factory, cx_factory, empire_factory):
        user = user_factory(id=1)
        other_user = user_factory(id=2)
        cx = cx_factory(user=user)
        other_empire = empire_factory(user=other_user)

        url = reverse('planning:cx-junctions')
        payload = [{'cx_uuid': str(cx.uuid), 'empires': [{'empire_uuid': str(other_empire.uuid)}]}]

        response = api_client.as_user(user).post(url, data=payload, format='json')

        assert response.status_code == 403
        assert response.data['error'] == 'Invalid Empire UUIDs detected.'

    def test_sync_junctions_rejects_duplicate_empire_assignment(
        self, api_client, user_factory, cx_factory, empire_factory
    ):
        user = user_factory(id=1)
        cx_1 = cx_factory(user=user)
        cx_2 = cx_factory(user=user)
        empire = empire_factory(user=user)

        url = reverse('planning:cx-junctions')
        payload = [
            {'cx_uuid': str(cx_1.uuid), 'empires': [{'empire_uuid': str(empire.uuid)}]},
            {'cx_uuid': str(cx_2.uuid), 'empires': [{'empire_uuid': str(empire.uuid)}]},
        ]

        response = api_client.as_user(user).post(url, data=payload, format='json')

        assert response.status_code == 400
        assert response.data['error'] == 'Duplicate empire assignment in request.'
