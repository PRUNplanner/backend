import pytest
from django.urls import reverse
from tests.fixtures.planning.fxt_plan_vallis import plan_data_vallis

pytestmark = pytest.mark.django_db


class TestSharedViewSet:
    def test_shared_get(self, api_client, user_factory, plan_factory, shared_factory):

        url_list = reverse('planning:shared')

        # must be authorized
        response_noauth = api_client.get(url_list)
        assert response_noauth.status_code == 401

        user_1 = user_factory(id=1)
        plan_1 = plan_factory(user=user_1, plan_data=plan_data_vallis)
        plan_2 = plan_factory(user=user_1, plan_data=plan_data_vallis)
        shared_1 = shared_factory(user=user_1, plan=plan_1)
        _shared_2 = shared_factory(user=user_1, plan=plan_2)

        # retrieve list
        response_list = api_client.as_user(user_1).get(url_list)
        assert response_list.status_code == 200
        assert len(response_list.data) == 2

        # retrieve individual - 404
        url_get_404 = reverse('planning:shared-detail', kwargs={'pk': '356da85a-494a-45a9-b20e-16d0f128c5b8'})
        response_404 = api_client.get(url_get_404)
        assert response_404.status_code == 404

        # retrieve individual - exists
        url_get = reverse('planning:shared-detail', kwargs={'pk': shared_1.uuid})
        response_get = api_client.get(url_get)
        assert response_get.status_code == 200
        assert response_get.data['uuid'] == str(shared_1.uuid)

    def test_shared_delete(self, api_client, user_factory, plan_factory, shared_factory):

        user_1 = user_factory(id=1)
        plan_1 = plan_factory(user=user_1, plan_data=plan_data_vallis)
        plan_2 = plan_factory(user=user_1, plan_data=plan_data_vallis)
        shared_1 = shared_factory(user=user_1, plan=plan_1)
        _shared_2 = shared_factory(user=user_1, plan=plan_2)

        # destroy
        url_destroy = reverse('planning:shared-detail', kwargs={'pk': str(shared_1.uuid)})

        response_destroy_noauth = api_client.delete(url_destroy)
        assert response_destroy_noauth.status_code == 401

        response_destroy = api_client.as_user(user_1).delete(url_destroy)
        assert response_destroy.status_code == 204

    def test_shared_create(self, api_client, user_factory, plan_factory, shared_factory):

        user_1 = user_factory(id=1)
        plan_1 = plan_factory(user=user_1, plan_data=plan_data_vallis)

        # destroy
        url_create = reverse('planning:shared')
        post_data = {'plan': str(plan_1.uuid)}

        response_create_noauth = api_client.post(url_create, data=post_data, format='json')
        assert response_create_noauth.status_code == 401

        response_create = api_client.as_user(user_1).post(url_create, data=post_data, format='json')
        assert response_create.status_code == 201

        # retrieve list
        url_list = reverse('planning:shared')
        response_list = api_client.as_user(user_1).get(url_list)
        assert response_list.status_code == 200
        assert len(response_list.data) == 1

    def test_shared_clone(self, api_client, user_factory, plan_factory, shared_factory):

        user_1 = user_factory(id=1)
        plan_1 = plan_factory(user=user_1, plan_data=plan_data_vallis)
        shared_1 = shared_factory(user=user_1, plan=plan_1)

        url_clone = reverse('planning:shared-clone', kwargs={'pk': str(shared_1.uuid)})

        # clone no auth
        response_clone_noauth = api_client.post(url_clone)
        assert response_clone_noauth.status_code == 401

        # clone auth
        response_clone = api_client.as_user(user_1).post(url_clone)
        assert response_clone.status_code == 201
