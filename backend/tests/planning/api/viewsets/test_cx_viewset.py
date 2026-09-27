import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
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

    def test_create_rejects_empty_ticker(self, api_client, user_factory):
        user = user_factory(id=1)
        cx_data = {'ticker_empire': [{'ticker': '', 'type': 'BUY', 'value': 1}]}

        response = api_client.as_user(user).post(
            reverse('planning:cx'), data={'cx_name': 'New CX', 'cx_data': cx_data}, format='json'
        )

        assert response.status_code == 400

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

    @pytest.mark.usefixtures('locmem_cache')
    def test_sync_junctions_assigns_empire_to_cx(self, api_client, user_factory, cx_factory, empire_factory):
        user = user_factory(id=1)
        cx = cx_factory(user=user, cx_name='My CX')
        empire = empire_factory(user=user)

        empire_list_url = reverse('planning:empire')
        api_client.as_user(user).get(empire_list_url)
        assert api_client.as_user(user).get(empire_list_url)['X-Cache-Hit'] == '1'

        url = reverse('planning:cx-junctions')
        payload = [{'cx_uuid': str(cx.uuid), 'empires': [{'empire_uuid': str(empire.uuid)}]}]

        response = api_client.as_user(user).post(url, data=payload, format='json')

        assert response.status_code == 200
        assert [e['uuid'] for e in response.data[0]['empires']] == [str(empire.uuid)]

        # the cached empire list nests the cx, so it must reflect the new assignment
        assert api_client.as_user(user).get(empire_list_url).data[0]['cx']['uuid'] == str(cx.uuid)

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


class TestCXViewSetQueries:
    @pytest.mark.parametrize('empire_count', [1, 5])
    def test_list_query_count_is_constant(
        self, empire_count, api_client, user_factory, cx_factory, empire_factory, plan_factory
    ):
        user = user_factory(id=1)
        cx = cx_factory(user=user)
        for _ in range(empire_count):
            empire = empire_factory(user=user, cx=cx)
            empire.plans.add(plan_factory(user=user), through_defaults={'user': user})

        with CaptureQueriesContext(connection) as ctx:
            response = api_client.as_user(user).get(reverse('planning:cx'))

        assert len(response.data[0]['empires']) == empire_count
        # cx, empires prefetch, plans prefetch
        assert len(ctx.captured_queries) == 3

    def test_retrieve_query_count_is_constant(self, api_client, user_factory, cx_factory, empire_factory):
        user = user_factory(id=1)
        cx = cx_factory(user=user)
        for _ in range(5):
            empire_factory(user=user, cx=cx)

        with CaptureQueriesContext(connection) as ctx:
            api_client.as_user(user).get(reverse('planning:cx-detail', kwargs={'pk': str(cx.uuid)}))

        assert len(ctx.captured_queries) == 3

    def test_list_does_not_load_empire_state(self, api_client, user_factory, cx_factory, empire_factory):
        user = user_factory(id=1)
        empire_factory(user=user, cx=cx_factory(user=user))

        with CaptureQueriesContext(connection) as ctx:
            api_client.as_user(user).get(reverse('planning:cx'))

        assert not any('empire_state' in q['sql'] for q in ctx.captured_queries)
