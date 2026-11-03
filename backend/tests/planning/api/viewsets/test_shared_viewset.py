import pytest
from api.renderers import OrjsonRenderer
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from planning.api.viewsets.shared_viewset import SharedViewSet
from planning.models import PlanningShared
from rest_framework.renderers import BrowsableAPIRenderer
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken
from tests.fixtures.planning.fxt_plan_vallis import plan_data_vallis
from user.models import UserAPIKey

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
        plan_1 = plan_factory(user=user_1, plan_name='Vallis Base', plan_data=plan_data_vallis)
        shared_1 = shared_factory(user=user_1, plan=plan_1)

        url_clone = reverse('planning:shared-clone', kwargs={'pk': str(shared_1.uuid)})

        # clone no auth
        response_clone_noauth = api_client.post(url_clone)
        assert response_clone_noauth.status_code == 401

        # clone auth
        response_clone = api_client.as_user(user_1).post(url_clone)
        assert response_clone.status_code == 201
        assert response_clone.data['plan_name'] == 'Vallis Base (Clone)'

    def test_shared_clone_keeps_long_names_within_limit(self, api_client, user_factory, plan_factory, shared_factory):
        user_1 = user_factory(id=1)
        plan_1 = plan_factory(user=user_1, plan_name='x' * 200, plan_data=plan_data_vallis)
        shared_1 = shared_factory(user=user_1, plan=plan_1)

        response_clone = api_client.as_user(user_1).post(
            reverse('planning:shared-clone', kwargs={'pk': str(shared_1.uuid)})
        )

        assert response_clone.status_code == 201
        assert response_clone.data['plan_name'] == f'{"x" * 192} (Clone)'


class TestSharedViewSetRetrieve:
    @staticmethod
    def _share_with_empires(user, plan_factory, empire_factory, cx_factory, shared_factory, empire_count: int):
        plan = plan_factory(user=user, plan_data=plan_data_vallis)
        for _ in range(empire_count):
            empire = empire_factory(user=user, cx=cx_factory(user=user))
            empire.plans.add(plan, through_defaults={'user': user})
        return shared_factory(user=user, plan=plan)

    def test_retrieve_query_count_is_constant(
        self, api_client, user_factory, plan_factory, empire_factory, cx_factory, shared_factory
    ):
        user = user_factory(id=1)
        share_small = self._share_with_empires(user, plan_factory, empire_factory, cx_factory, shared_factory, 1)
        share_large = self._share_with_empires(user, plan_factory, empire_factory, cx_factory, shared_factory, 4)

        query_counts = []
        for share in (share_small, share_large):
            with CaptureQueriesContext(connection) as ctx:
                response = api_client.get(reverse('planning:shared-detail', kwargs={'pk': share.uuid}))
            assert response.status_code == 200
            query_counts.append(len(ctx.captured_queries))

        assert query_counts[0] == query_counts[1]

    def test_retrieve_hides_owner_empires(
        self, api_client, user_factory, plan_factory, empire_factory, cx_factory, shared_factory
    ):
        user = user_factory(id=1)
        share = self._share_with_empires(user, plan_factory, empire_factory, cx_factory, shared_factory, 2)

        response = api_client.get(reverse('planning:shared-detail', kwargs={'pk': share.uuid}))

        assert response.status_code == 200
        assert set(response.data['plan_details']) == {
            'uuid',
            'plan_name',
            'planet_natural_id',
            'plan_permits_used',
            'plan_cogc',
            'plan_corphq',
            'plan_data',
        }

    def test_retrieve_counts_views(self, api_client, user_factory, plan_factory, shared_factory):
        user = user_factory(id=1)
        share = shared_factory(user=user, plan=plan_factory(user=user, plan_data=plan_data_vallis), view_count=0)
        url = reverse('planning:shared-detail', kwargs={'pk': share.uuid})

        api_client.get(url)
        response = api_client.get(url)

        assert response.data['view_count'] == 2


class TestSharedViewSetOwnership:
    def test_create_rejects_another_users_plan(self, api_client, user_factory, plan_factory):
        owner, stranger = user_factory(), user_factory()
        plan = plan_factory(user=owner, plan_data=plan_data_vallis)

        response = api_client.as_user(stranger).post(
            reverse('planning:shared'), data={'plan': str(plan.uuid)}, format='json'
        )

        assert response.status_code == 400
        assert 'plan' in response.data
        assert not PlanningShared.objects.exists()

    def test_list_only_returns_own_shares(self, api_client, user_factory, plan_factory, shared_factory):
        owner, stranger = user_factory(), user_factory()
        share = shared_factory(user=owner, plan=plan_factory(user=owner, plan_data=plan_data_vallis))
        shared_factory(user=stranger, plan=plan_factory(user=stranger, plan_data=plan_data_vallis))

        response = api_client.as_user(owner).get(reverse('planning:shared'))

        assert response.status_code == 200
        assert [row['uuid'] for row in response.data] == [str(share.uuid)]


@pytest.mark.usefixtures('locmem_cache')
class TestSharedViewSetThrottle:
    @pytest.fixture
    def url(self, user_factory, plan_factory, shared_factory):
        user = user_factory()
        share = shared_factory(user=user, plan=plan_factory(user=user, plan_data=plan_data_vallis))
        return reverse('planning:shared-detail', kwargs={'pk': share.uuid})

    def test_anonymous_views_are_limited_per_ip(self, url):
        client = APIClient(REMOTE_ADDR='10.0.0.1')
        for _ in range(120):
            assert client.get(url).status_code == 200

        throttled = client.get(url)

        assert throttled.status_code == 429
        assert int(throttled['Retry-After']) > 0
        assert APIClient(REMOTE_ADDR='10.0.0.2').get(url).status_code == 200

    def test_logged_in_viewers_keep_their_user_ceiling(self, api_client, user_factory, url):
        client = api_client.as_user(user_factory())
        for _ in range(121):
            assert client.get(url).status_code == 200


class TestSharedHtml:
    """The browsable API's share form must never list another user's plans (SEC-01)."""

    @pytest.fixture
    def plans(self, user_factory, plan_factory):
        owner = user_factory()
        other = user_factory()
        return owner, plan_factory(user=owner), plan_factory(user=other)

    def _auth(self, api_client, role, user):
        if role == 'jwt':
            api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')
        elif role == 'api_key':
            _, key = UserAPIKey.objects.create_key(name='script', user=user)
            api_client.credentials(HTTP_AUTHORIZATION=f'Api-Key {key}')
        elif role == 'staff':
            user.is_staff = True
            user.save()
            api_client.force_authenticate(user=user)

    @pytest.mark.parametrize('role', ['anonymous', 'jwt', 'api_key', 'staff'])
    def test_production_serves_no_html(self, api_client, plans, role):
        owner, own_plan, other_plan = plans
        self._auth(api_client, role, owner)
        url = reverse('planning:shared')

        # ?format=api names a renderer that production doesn't have
        response = api_client.get(url, {'format': 'api'})
        assert response.status_code == 404
        assert 'text/html' not in response['Content-Type']

        # HTML only: nothing to offer
        response = api_client.get(url, HTTP_ACCEPT='text/html')
        assert response.status_code == 406
        assert response['Content-Type'] == 'application/json'

        # a browser's Accept header gets JSON
        response = api_client.get(url, HTTP_ACCEPT='text/html,application/xhtml+xml,*/*;q=0.8')
        assert response['Content-Type'] == 'application/json'
        assert response.status_code == (401 if role == 'anonymous' else 200)
        assert str(other_plan.uuid) not in response.content.decode()

    def test_local_form_lists_only_own_plans(self, api_client, plans, monkeypatch):
        # views bind renderer_classes at import, so give the view local.py's renderers directly
        monkeypatch.setattr(SharedViewSet, 'renderer_classes', [OrjsonRenderer, BrowsableAPIRenderer])
        owner, own_plan, other_plan = plans
        api_client.force_authenticate(user=owner)

        response = api_client.get(reverse('planning:shared'), {'format': 'api'})

        assert response.status_code == 200
        assert response['Content-Type'].startswith('text/html')
        content = response.content.decode()
        assert f'value="{own_plan.uuid}"' in content
        assert str(other_plan.uuid) not in content
