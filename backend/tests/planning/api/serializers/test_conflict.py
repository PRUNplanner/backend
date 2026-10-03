import importlib
from dataclasses import dataclass
from datetime import timedelta

import pytest
from django.apps import apps
from django.db.models import Model
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker
from planning.models import PlanningEmpire, PlanningFactionChoices
from rest_framework.test import APIClient
from tests.fixtures.planning.fxt_plan_vallis import plan_data_vallis
from user.models import User

pytestmark = pytest.mark.django_db


@dataclass(frozen=True)
class Resource:
    name: str
    model: str
    list_url: str
    detail_url: str
    name_field: str
    payload: dict[str, object]


RESOURCES = [
    Resource(
        'plan',
        'planning.PlanningPlan',
        'planning:plan',
        'planning:plan-detail',
        'plan_name',
        {
            'plan_name': 'Plan',
            'planet_natural_id': 'OT-580b',
            'plan_permits_used': 1,
            'plan_corphq': False,
            'plan_data': plan_data_vallis,
        },
    ),
    Resource(
        'empire',
        'planning.PlanningEmpire',
        'planning:empire',
        'planning:empire-detail',
        'empire_name',
        {
            'empire_name': 'Empire',
            'empire_faction': PlanningFactionChoices.ANTARES,
            'empire_permits_used': 1,
            'empire_permits_total': 2,
        },
    ),
    Resource(
        'cx', 'planning.PlanningCX', 'planning:cx', 'planning:cx-detail', 'cx_name', {'cx_name': 'CX', 'cx_data': {}}
    ),
]


@pytest.fixture(params=RESOURCES, ids=lambda r: r.name)
def resource(request: pytest.FixtureRequest) -> Resource:
    return request.param


@pytest.fixture
def owner() -> User:
    return baker.make('user.User')


@pytest.fixture
def obj(resource: Resource, owner: User) -> Model:
    if resource.name == 'plan':
        return baker.make(resource.model, user=owner, plan_data=plan_data_vallis)
    return baker.make(resource.model, user=owner)


def _client(api_client: APIClient, user: User) -> APIClient:
    return api_client.as_user(user)  # ty:ignore[unresolved-attribute]


def _url(resource: Resource, obj: Model) -> str:
    return reverse(resource.detail_url, kwargs={'pk': str(obj.pk)})


def _put(api_client: APIClient, user: User, resource: Resource, obj: Model, name: str, **extra: object):
    return _client(api_client, user).put(
        _url(resource, obj), data={**resource.payload, resource.name_field: name, **extra}, format='json'
    )


class TestModifiedAt:
    def test_list_and_detail_carry_modified_at(self, api_client, resource, owner, obj) -> None:
        listed = _client(api_client, owner).get(reverse(resource.list_url)).data
        detail = _client(api_client, owner).get(_url(resource, obj)).data

        assert listed[0]['modified_at'] == detail['modified_at']
        assert detail['modified_at']
        assert 'base_modified_at' not in detail

    def test_plan_list_of_empire_carries_modified_at(self, api_client, owner) -> None:
        empire: PlanningEmpire = baker.make('planning.PlanningEmpire', user=owner)
        plan = baker.make('planning.PlanningPlan', user=owner, plan_data=plan_data_vallis)
        empire.plans.add(plan, through_defaults={'user': owner})

        response = _client(api_client, owner).get(reverse('planning:empire-plan-list', kwargs={'pk': str(empire.pk)}))

        assert response.data[0]['modified_at']


class TestConditionalSave:
    def test_current_base_saves_and_returns_newer_version(self, api_client, resource, owner, obj) -> None:
        base = _client(api_client, owner).get(_url(resource, obj)).data['modified_at']

        response = _put(api_client, owner, resource, obj, 'Renamed', base_modified_at=base)

        assert response.status_code == 200
        assert response.data[resource.name_field] == 'Renamed'
        assert response.data['modified_at'] > base

    def test_outdated_base_is_a_conflict(self, api_client, resource, owner, obj) -> None:
        current = _client(api_client, owner).get(_url(resource, obj)).data['modified_at']
        older = (timezone.now() - timedelta(hours=1)).isoformat()

        response = _put(api_client, owner, resource, obj, 'Renamed', base_modified_at=older)

        assert response.status_code == 409
        assert response.data == {'detail': 'Changed since it was loaded.', 'code': 'conflict', 'modified_at': current}
        obj.refresh_from_db()
        assert getattr(obj, resource.name_field) != 'Renamed'

    def test_second_save_from_same_base_conflicts(self, api_client, resource, owner, obj) -> None:
        base = _client(api_client, owner).get(_url(resource, obj)).data['modified_at']

        first = _put(api_client, owner, resource, obj, 'Tab B', base_modified_at=base)
        second = _put(api_client, owner, resource, obj, 'Tab A', base_modified_at=base)

        assert first.status_code == 200
        assert second.status_code == 409
        assert second.data['modified_at'] == first.data['modified_at']
        obj.refresh_from_db()
        assert getattr(obj, resource.name_field) == 'Tab B'

    @pytest.mark.parametrize('extra', [{}, {'base_modified_at': None}], ids=['absent', 'null'])
    def test_without_base_overwrites(self, api_client, resource, owner, obj, extra) -> None:
        _put(api_client, owner, resource, obj, 'Tab B')

        response = _put(api_client, owner, resource, obj, 'Tab A', **extra)

        assert response.status_code == 200
        assert response.data[resource.name_field] == 'Tab A'

    def test_deleted_object_is_404(self, api_client, resource, owner, obj) -> None:
        base = _client(api_client, owner).get(_url(resource, obj)).data['modified_at']
        url = _url(resource, obj)
        obj.delete()

        response = _client(api_client, owner).put(
            url, data={**resource.payload, 'base_modified_at': base}, format='json'
        )

        assert response.status_code == 404

    def test_create_ignores_base(self, api_client, resource, owner) -> None:
        response = _client(api_client, owner).post(
            reverse(resource.list_url),
            data={**resource.payload, 'base_modified_at': '2020-01-01T00:00:00Z'},
            format='json',
        )

        assert response.status_code == 201
        assert response.data['modified_at']

    @pytest.mark.usefixtures('locmem_cache')
    def test_get_after_save_returns_new_version(
        self, api_client, resource, owner, obj, django_capture_on_commit_callbacks
    ) -> None:
        client = _client(api_client, owner)
        base = client.get(_url(resource, obj)).data['modified_at']
        client.get(reverse(resource.list_url))
        assert client.get(_url(resource, obj))['X-Cache-Hit'] == '1'

        with django_capture_on_commit_callbacks(execute=True):
            saved = _put(api_client, owner, resource, obj, 'Renamed', base_modified_at=base)

        assert client.get(_url(resource, obj)).data['modified_at'] == saved.data['modified_at']
        assert client.get(reverse(resource.list_url)).data[0]['modified_at'] == saved.data['modified_at']


class TestEmpireConfigVersion:
    def test_state_sync_and_cx_junctions_keep_the_version(self, api_client, owner) -> None:
        empire: PlanningEmpire = baker.make('planning.PlanningEmpire', user=owner)
        cx = baker.make('planning.PlanningCX', user=owner)
        client = _client(api_client, owner)
        url = reverse('planning:empire-detail', kwargs={'pk': str(empire.pk)})
        base = client.get(url).data['modified_at']

        state = {
            'metadata': {
                'faction': PlanningFactionChoices.ANTARES,
                'permits_used': 1,
                'permits_total': 2,
                'plan_count': 0,
                'timestamp': '2026-01-01T00:00:00Z',
            },
            'empire_total': {},
            'plan_details': {},
        }
        synced = client.patch(
            reverse('planning:empire-sync-state', kwargs={'pk': str(empire.pk)}), data=state, format='json'
        )
        junctions = client.post(
            reverse('planning:cx-junctions'),
            data=[{'cx_uuid': str(cx.pk), 'empires': [{'empire_uuid': str(empire.pk)}]}],
            format='json',
        )

        assert synced.status_code == 200
        assert junctions.status_code == 200
        assert synced.data['modified_at'] == base
        assert client.get(url).data['modified_at'] == base

        response = client.put(url, data={**RESOURCES[1].payload, 'base_modified_at': base}, format='json')
        assert response.status_code == 200


class TestEmpireConfigBackfill:
    def test_backfill_copies_modified_at(self, owner) -> None:
        migration = importlib.import_module('planning.migrations.0009_empire_config_modified_at_backfill')
        empire: PlanningEmpire = baker.make('planning.PlanningEmpire', user=owner)
        PlanningEmpire.objects.filter(pk=empire.pk).update(config_modified_at=timezone.now() + timedelta(days=1))

        migration.backfill(apps, None)

        empire.refresh_from_db()
        assert empire.config_modified_at == empire.modified_at
