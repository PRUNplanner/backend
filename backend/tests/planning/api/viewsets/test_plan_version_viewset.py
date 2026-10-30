import random
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from core.services.cache_manager import CacheManager
from django.core.cache.backends.locmem import LocMemCache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from model_bakery import baker
from planning.models import PlanningPlan, PlanningPlanVersion
from planning.services import plan_version_service
from rest_framework.test import APIClient
from tests.fixtures.planning.fxt_plan_vallis import plan_data_vallis
from user.models import User

pytestmark = pytest.mark.django_db

SNAPSHOT = plan_version_service.SNAPSHOT_FIELDS


def payload(**overrides: object) -> dict[str, object]:
    return {
        'plan_name': 'My Plan',
        'planet_natural_id': 'OT-580b',
        'plan_permits_used': 1,
        'plan_cogc': '---',
        'plan_corphq': False,
        'plan_data': plan_data_vallis,
        **overrides,
    }


def snapshot(obj: PlanningPlan | PlanningPlanVersion) -> dict[str, object]:
    return {field: getattr(obj, field) for field in SNAPSHOT}


@pytest.fixture
def owner() -> User:
    return baker.make(User)


@pytest.fixture
def plan(owner: User) -> PlanningPlan:
    return baker.make(PlanningPlan, user=owner, plan_name='Base', plan_permits_used=1, plan_cogc='---', plan_data={})


@pytest.fixture
def client(api_client: APIClient, owner: User) -> APIClient:
    return api_client.as_user(owner)  # ty:ignore[unresolved-attribute]


def detail(plan: PlanningPlan) -> str:
    return reverse('planning:plan-detail', kwargs={'pk': plan.pk})


def versions_url(plan: PlanningPlan) -> str:
    return reverse('planning:plan-versions', kwargs={'plan_pk': plan.pk})


def version_url(version: PlanningPlanVersion, name: str = 'planning:plan-version-detail') -> str:
    return reverse(name, kwargs={'plan_pk': version.plan_id, 'pk': version.pk})  # ty:ignore[unresolved-attribute]


def draft_url(plan: PlanningPlan) -> str:
    return reverse('planning:plan-draft', kwargs={'plan_pk': plan.pk})


def saved(plan: PlanningPlan) -> list[PlanningPlanVersion]:
    return list(PlanningPlanVersion.objects.filter(plan=plan).order_by('-created_at'))


def save(client: APIClient, plan: PlanningPlan, **overrides: object) -> PlanningPlanVersion:
    response = client.put(detail(plan), payload(**overrides), format='json')
    assert response.status_code == 200, response.data
    return saved(plan)[0]


class TestPlanSave:
    def test_first_put_leaves_baseline_and_save(self, client: APIClient, plan: PlanningPlan) -> None:
        before = snapshot(plan)

        response = client.put(detail(plan), payload(plan_name='Saved'), format='json')

        assert response.status_code == 200
        newest, baseline = saved(plan)
        assert (newest.kind, baseline.kind) == ('save', 'baseline')
        assert snapshot(baseline) == before
        plan.refresh_from_db()
        assert plan.plan_name == 'Saved'
        assert snapshot(newest) == snapshot(plan)

    def test_response_has_no_version_fields(self, client: APIClient, plan: PlanningPlan) -> None:
        response = client.put(detail(plan), payload(version_name='Early', restored_from=None), format='json')

        assert response.status_code == 200
        assert 'version_name' not in response.data
        assert 'restored_from' not in response.data

    def test_version_name(self, client: APIClient, plan: PlanningPlan) -> None:
        assert save(client, plan, version_name='Early').name == 'Early'
        assert save(client, plan, version_name='').name == ''

    def test_version_name_too_long(self, client: APIClient, plan: PlanningPlan) -> None:
        response = client.put(detail(plan), payload(version_name='x' * 81), format='json')

        assert response.status_code == 400
        assert 'version_name' in response.data
        assert saved(plan) == []

    def test_restored_from(self, client: APIClient, plan: PlanningPlan) -> None:
        source = save(client, plan, plan_name='Early')
        save(client, plan, plan_name='Late')

        restored = save(client, plan, plan_name='Early', restored_from=str(source.pk))

        assert restored.kind == 'restore'
        assert restored.source_version == source

    @pytest.mark.parametrize('kind', ['unknown', 'other_plan', 'draft'])
    def test_restored_from_must_be_a_version_of_this_plan(
        self, client: APIClient, plan: PlanningPlan, owner: User, kind: str
    ) -> None:
        other = baker.make(PlanningPlan, user=owner, plan_permits_used=1, plan_data={})
        uuid = {
            'unknown': lambda: uuid4(),
            'other_plan': lambda: plan_version_service.record_created(other).pk,
            'draft': lambda: plan_version_service.save_draft(plan, snapshot(plan), plan.modified_at).pk,
        }[kind]()

        response = client.put(detail(plan), payload(plan_name='New', restored_from=str(uuid)), format='json')

        assert response.status_code == 400
        assert 'restored_from' in response.data
        plan.refresh_from_db()
        assert plan.plan_name == 'Base'
        assert saved(plan) == []

    def test_failed_version_insert_fails_the_put(self, client: APIClient, plan: PlanningPlan) -> None:
        with (
            patch.object(plan_version_service, '_insert', side_effect=RuntimeError('insert failed')),
            pytest.raises(RuntimeError),
        ):
            client.put(detail(plan), payload(plan_name='Lost'), format='json')

        plan.refresh_from_db()
        assert plan.plan_name == 'Base'
        assert saved(plan) == []

    def test_conflict_creates_no_version_and_keeps_the_draft(self, client: APIClient, plan: PlanningPlan) -> None:
        stale = client.get(detail(plan)).data['modified_at']
        save(client, plan, plan_name='Other tab')
        count = len(saved(plan))
        plan_version_service.save_draft(plan, snapshot(plan), plan.modified_at)

        response = client.put(detail(plan), payload(plan_name='Mine', base_modified_at=stale), format='json')

        assert response.status_code == 409
        assert len(saved(plan)) == count
        assert PlanningPlanVersion.drafts.filter(plan=plan).exists()

    def test_save_deletes_the_draft(self, client: APIClient, plan: PlanningPlan) -> None:
        draft = plan_version_service.save_draft(plan, snapshot(plan), plan.modified_at)

        save(client, plan, draft_updated_at=draft.updated_at.isoformat())

        assert not PlanningPlanVersion.drafts.filter(plan=plan).exists()

    def test_create_records_one_version_and_ignores_the_save_fields(self, client: APIClient) -> None:
        response = client.post(
            reverse('planning:plan'), payload(version_name='Early', restored_from=str(uuid4())), format='json'
        )

        assert response.status_code == 201
        plan = PlanningPlan.objects.get(pk=response.data['uuid'])
        assert [(v.kind, v.name) for v in saved(plan)] == [('save', '')]

    def test_copies_start_a_fresh_history(self, client: APIClient, plan: PlanningPlan) -> None:
        save(client, plan)
        share = baker.make('planning.PlanningShared', user=plan.user, plan=plan)

        clone = client.post(reverse('planning:plan-clone', kwargs={'pk': plan.pk}))
        shared_clone = client.post(reverse('planning:shared-clone', kwargs={'pk': share.pk}))

        assert (clone.status_code, shared_clone.status_code) == (201, 201)
        for uuid in (clone.data['uuid'], shared_clone.data['uuid']):
            assert [v.kind for v in saved(PlanningPlan.objects.get(pk=uuid))] == ['save']


@pytest.mark.parametrize('seed', range(5))
def test_plan_row_always_equals_the_newest_version(client: APIClient, plan: PlanningPlan, seed: int) -> None:
    rng = random.Random(seed)

    def any_version() -> PlanningPlanVersion:
        return rng.choice(saved(plan))

    def do_save() -> None:
        save(client, plan, plan_name=f'Save {rng.random()}', plan_permits_used=rng.randint(1, 3))
        assert len([v for v in saved(plan) if not v.name]) <= plan_version_service.KEEP_UNNAMED

    def do_named_save() -> None:
        save(client, plan, plan_name=f'Named {rng.random()}', version_name=f'Name {rng.random()}')

    def do_restore() -> None:
        source = any_version()
        save(client, plan, plan_name=source.plan_name, restored_from=str(source.pk))

    def do_rename() -> None:
        response = client.patch(version_url(any_version()), {'name': rng.choice(['', 'Kept'])}, format='json')
        assert response.status_code == 200

    def do_admin_restore() -> None:
        plan_version_service.restore_version(any_version())

    do_save()
    for _ in range(40):
        rng.choice([do_save, do_named_save, do_restore, do_rename, do_admin_restore])()

        plan.refresh_from_db()
        assert snapshot(saved(plan)[0]) == snapshot(plan)


class TestVersions:
    def test_list(self, client: APIClient, plan: PlanningPlan) -> None:
        early = save(client, plan, version_name='Early')
        late = save(client, plan)
        plan_version_service.save_draft(plan, snapshot(plan), plan.modified_at)

        with CaptureQueriesContext(connection) as queries:
            response = client.get(versions_url(plan))

        assert response.status_code == 200
        assert [row['uuid'] for row in response.data[:2]] == [str(late.pk), str(early.pk)]
        assert [row['kind'] for row in response.data] == ['save', 'save', 'baseline']
        assert [row['is_active'] for row in response.data] == [True, False, False]
        assert set(response.data[0]) == {
            'uuid',
            'kind',
            'name',
            'created_at',
            'source_version',
            'is_active',
            'data_bytes',
        }
        assert not any('"plan_data"' in q['sql'] and 'plan_versions' in q['sql'] for q in queries)
        assert len(queries) == 2

    def test_list_of_a_plan_without_versions(self, client: APIClient, plan: PlanningPlan) -> None:
        response = client.get(versions_url(plan))

        assert (response.status_code, response.data) == (200, [])

    def test_detail(self, client: APIClient, plan: PlanningPlan) -> None:
        version = save(client, plan, plan_name='Early')

        response = client.get(version_url(version))

        assert response.status_code == 200
        assert response.data['uuid'] == str(version.pk)
        assert response.data['is_active'] is True
        assert response.data['plan_name'] == 'Early'
        assert response.data['plan_data'] == version.plan_data
        assert response.data['planet_natural_id'] == 'OT-580b'

    def test_detail_returns_an_old_snapshot_as_stored(self, client: APIClient, plan: PlanningPlan) -> None:
        version = save(client, plan)
        PlanningPlanVersion.objects.filter(pk=version.pk).update(plan_data={'old_schema': True})

        response = client.get(version_url(version))

        assert response.status_code == 200
        assert response.data['plan_data'] == {'old_schema': True}

    def test_draft_and_other_plans_versions_are_not_found(
        self, client: APIClient, plan: PlanningPlan, owner: User
    ) -> None:
        draft = plan_version_service.save_draft(plan, snapshot(plan), plan.modified_at)
        other = plan_version_service.record_created(
            baker.make(PlanningPlan, user=owner, plan_permits_used=1, plan_data={})
        )

        assert client.get(version_url(draft)).status_code == 404
        cross = reverse('planning:plan-version-detail', kwargs={'plan_pk': plan.pk, 'pk': other.pk})
        assert client.get(cross).status_code == 404

    def test_rename(self, client: APIClient, plan: PlanningPlan) -> None:
        version = save(client, plan)

        response = client.patch(version_url(version), {'name': '  Early  '}, format='json')

        assert response.status_code == 200
        assert response.data['name'] == 'Early'
        assert response.data['is_active'] is True
        assert 'plan_data' not in response.data
        version.refresh_from_db()
        assert version.name == 'Early'

        assert client.patch(version_url(version), {'name': ''}, format='json').data['name'] == ''

    @pytest.mark.parametrize('body', [{}, {'name': 'x' * 81}, {'name': None}])
    def test_rename_validates(self, client: APIClient, plan: PlanningPlan, body: dict[str, object]) -> None:
        version = save(client, plan)

        response = client.patch(version_url(version), body, format='json')

        assert response.status_code == 400
        assert 'name' in response.data

    def test_clone(self, client: APIClient, plan: PlanningPlan) -> None:
        version = save(client, plan, plan_name='Early', version_name='Stage 1')
        save(client, plan, plan_name='Late')

        response = client.post(version_url(version, 'planning:plan-version-clone'))

        assert response.status_code == 201
        assert response.data['plan_name'] == 'Early (Stage 1)'
        clone = PlanningPlan.objects.get(pk=response.data['uuid'])
        assert clone.user == plan.user
        assert [v.kind for v in saved(clone)] == ['save']
        plan.refresh_from_db()
        assert plan.plan_name == 'Late'
        assert len(saved(plan)) == 3

    def test_endpoints_do_not_clear_the_planning_cache(self, client: APIClient, plan: PlanningPlan) -> None:
        version = save(client, plan)

        with patch.object(CacheManager, 'invalidate_on_commit') as invalidate:
            client.get(versions_url(plan))
            client.get(version_url(version))
            client.patch(version_url(version), {'name': 'Early'}, format='json')

        invalidate.assert_not_called()


class TestDraft:
    def body(self, plan: PlanningPlan, **overrides: object) -> dict[str, object]:
        return payload(**{'base_modified_at': plan.modified_at.isoformat(), 'draft_updated_at': None, **overrides})

    def test_no_draft(self, client: APIClient, plan: PlanningPlan) -> None:
        response = client.get(draft_url(plan))

        assert (response.status_code, response.data) == (200, {'draft': None})

    def test_put_then_get(self, client: APIClient, plan: PlanningPlan) -> None:
        put = client.put(draft_url(plan), self.body(plan, plan_name='Edited'), format='json')

        assert put.status_code == 200
        assert set(put.data) == {'updated_at'}

        draft = client.get(draft_url(plan)).data['draft']
        assert draft['plan_name'] == 'Edited'
        assert draft['plan_data'] == PlanningPlanVersion.drafts.get(plan=plan).plan_data
        assert draft['base_modified_at'] is not None
        assert draft['updated_at'] == put.data['updated_at']

    def test_two_puts_keep_one_row_and_leave_the_plan_alone(self, client: APIClient, plan: PlanningPlan) -> None:
        modified_at = plan.modified_at

        seen = None
        with patch.object(CacheManager, 'invalidate_on_commit') as invalidate:
            for name in ('First', 'Second'):
                body = self.body(plan, plan_name=name, draft_updated_at=seen)
                response = client.put(draft_url(plan), body, format='json')
                assert response.status_code == 200
                assert response.data['updated_at'] != seen
                seen = response.data['updated_at']

        invalidate.assert_not_called()
        assert PlanningPlanVersion.drafts.get(plan=plan).plan_name == 'Second'
        plan.refresh_from_db()
        assert plan.modified_at == modified_at
        assert plan.plan_name == 'Base'

    def test_null_cogc_falls_back_like_a_save(self, client: APIClient, plan: PlanningPlan) -> None:
        assert client.put(draft_url(plan), self.body(plan, plan_cogc=None), format='json').status_code == 200

        assert PlanningPlanVersion.drafts.get(plan=plan).plan_cogc == '---'

    def test_put_needs_a_base(self, client: APIClient, plan: PlanningPlan) -> None:
        response = client.put(draft_url(plan), payload(), format='json')

        assert response.status_code == 400
        assert 'base_modified_at' in response.data

    def test_put_needs_what_the_tab_last_saw(self, client: APIClient, plan: PlanningPlan) -> None:
        body = payload(base_modified_at=plan.modified_at.isoformat())

        response = client.put(draft_url(plan), body, format='json')

        assert response.status_code == 400
        assert 'draft_updated_at' in response.data

    def test_stale_tab_cannot_replace_the_draft(self, client: APIClient, plan: PlanningPlan) -> None:
        laptop = client.put(draft_url(plan), self.body(plan, plan_name='Laptop'), format='json').data['updated_at']

        for seen in (None, '2020-01-01T00:00:00Z'):
            response = client.put(
                draft_url(plan), self.body(plan, plan_name='Phone', draft_updated_at=seen), format='json'
            )

            assert response.status_code == 409
            assert response.data['code'] == 'draft_conflict'
            assert response.data['updated_at'] == laptop
        assert PlanningPlanVersion.drafts.get(plan=plan).plan_name == 'Laptop'

    def test_a_draft_needs_no_seen_value_when_none_is_stored(self, client: APIClient, plan: PlanningPlan) -> None:
        body = self.body(plan, draft_updated_at='2020-01-01T00:00:00Z')

        assert client.put(draft_url(plan), body, format='json').status_code == 200

    def test_put_validates_plan_data(self, client: APIClient, plan: PlanningPlan) -> None:
        response = client.put(draft_url(plan), self.body(plan, plan_data={'planet': 'nope'}), format='json')

        assert response.status_code == 400
        assert 'plan_data' in response.data
        assert not PlanningPlanVersion.drafts.filter(plan=plan).exists()

    def test_delete(self, client: APIClient, plan: PlanningPlan) -> None:
        seen = {'draft_updated_at': client.put(draft_url(plan), self.body(plan), format='json').data['updated_at']}

        assert client.delete(f'{draft_url(plan)}?{urlencode(seen)}').status_code == 204
        assert client.get(draft_url(plan)).data == {'draft': None}
        assert client.delete(f'{draft_url(plan)}?{urlencode(seen)}').status_code == 204

    def test_delete_needs_what_the_tab_last_saw(self, client: APIClient, plan: PlanningPlan) -> None:
        client.put(draft_url(plan), self.body(plan), format='json')

        response = client.delete(draft_url(plan))

        assert response.status_code == 400
        assert 'draft_updated_at' in response.data
        assert PlanningPlanVersion.drafts.filter(plan=plan).exists()

    def test_stale_tab_cannot_delete_the_draft(self, client: APIClient, plan: PlanningPlan) -> None:
        laptop = client.put(draft_url(plan), self.body(plan), format='json').data['updated_at']
        stale = urlencode({'draft_updated_at': '2020-01-01T00:00:00Z'})

        response = client.delete(f'{draft_url(plan)}?{stale}')

        assert response.status_code == 409
        assert response.data['updated_at'] == laptop
        assert PlanningPlanVersion.drafts.filter(plan=plan).exists()

    def test_save_keeps_a_draft_the_tab_never_saw(self, client: APIClient, plan: PlanningPlan) -> None:
        client.put(draft_url(plan), self.body(plan, plan_name='Laptop'), format='json')

        response = client.put(detail(plan), payload(plan_name='Phone', draft_updated_at=None), format='json')

        assert response.status_code == 200
        assert PlanningPlanVersion.drafts.get(plan=plan).plan_name == 'Laptop'
        assert saved(plan)[0].plan_name == 'Phone'

    def test_save_keeps_a_draft_newer_than_the_one_the_tab_saw(self, client: APIClient, plan: PlanningPlan) -> None:
        first = client.put(draft_url(plan), self.body(plan, plan_name='First'), format='json').data['updated_at']
        body = self.body(plan, plan_name='Newer', draft_updated_at=first)
        client.put(draft_url(plan), body, format='json')

        assert client.put(detail(plan), payload(draft_updated_at=first), format='json').status_code == 200

        assert PlanningPlanVersion.drafts.get(plan=plan).plan_name == 'Newer'

    def test_save_deletes_the_draft_the_tab_saw(self, client: APIClient, plan: PlanningPlan) -> None:
        seen = client.put(draft_url(plan), self.body(plan), format='json').data['updated_at']

        assert client.put(detail(plan), payload(draft_updated_at=seen), format='json').status_code == 200

        assert not PlanningPlanVersion.drafts.filter(plan=plan).exists()

    def test_save_without_the_field_keeps_the_draft(self, client: APIClient, plan: PlanningPlan) -> None:
        client.put(draft_url(plan), self.body(plan), format='json')

        assert client.put(detail(plan), payload(), format='json').status_code == 200

        assert PlanningPlanVersion.drafts.filter(plan=plan).exists()

    def test_seen_value_compares_as_an_instant(self, client: APIClient, plan: PlanningPlan) -> None:
        seen = client.put(draft_url(plan), self.body(plan), format='json').data['updated_at']
        offset = datetime.fromisoformat(seen).astimezone(timezone(timedelta(hours=2))).isoformat()

        assert client.put(draft_url(plan), self.body(plan, draft_updated_at=offset), format='json').status_code == 200

        seen = client.get(draft_url(plan)).data['draft']['updated_at']
        offset = datetime.fromisoformat(seen).astimezone(timezone(timedelta(hours=2))).isoformat()
        query = urlencode({'draft_updated_at': offset})
        assert client.delete(f'{draft_url(plan)}?{query}').status_code == 204

    def test_conflict_on_the_plan_leaves_the_draft(self, client: APIClient, plan: PlanningPlan) -> None:
        seen = client.put(draft_url(plan), self.body(plan), format='json').data['updated_at']
        stale = '2020-01-01T00:00:00Z'

        body = payload(base_modified_at=stale, draft_updated_at=seen)

        assert client.put(detail(plan), body, format='json').status_code == 409
        assert PlanningPlanVersion.drafts.filter(plan=plan).exists()

    def test_create_ignores_the_field(self, client: APIClient) -> None:
        response = client.post(reverse('planning:plan'), payload(draft_updated_at=None), format='json')

        assert response.status_code == 201

    def test_draft_is_not_in_the_versions_list(self, client: APIClient, plan: PlanningPlan) -> None:
        client.put(draft_url(plan), self.body(plan), format='json')

        assert client.get(versions_url(plan)).data == []

    def test_put_is_throttled_per_user(self, client: APIClient, plan: PlanningPlan, locmem_cache: LocMemCache) -> None:
        body = self.body(plan)
        for _ in range(60):
            assert client.put(draft_url(plan), body, format='json').status_code in (200, 409)

        assert client.put(draft_url(plan), body, format='json').status_code == 429
        assert client.get(draft_url(plan)).status_code == 200


@pytest.mark.parametrize(
    ('url', 'method'),
    [
        (lambda plan, version: versions_url(plan), 'get'),
        (lambda plan, version: version_url(version), 'get'),
        (lambda plan, version: version_url(version), 'patch'),
        (lambda plan, version: version_url(version, 'planning:plan-version-clone'), 'post'),
        (lambda plan, version: draft_url(plan), 'get'),
        (lambda plan, version: draft_url(plan), 'put'),
        (lambda plan, version: draft_url(plan), 'delete'),
    ],
)
class TestAccess:
    def test_anonymous(self, api_client: APIClient, plan: PlanningPlan, url: Callable[..., str], method: str) -> None:
        version = plan_version_service.record_created(plan)

        assert getattr(api_client, method)(url(plan, version), {}, format='json').status_code == 401

    def test_another_users_plan_is_not_found(
        self, api_client: APIClient, plan: PlanningPlan, url: Callable[..., str], method: str
    ) -> None:
        version = plan_version_service.record_created(plan)
        draft = plan_version_service.save_draft(plan, snapshot(plan), plan.modified_at)
        client = api_client.as_user(baker.make(User))  # ty:ignore[unresolved-attribute]

        response = getattr(client, method)(url(plan, version), payload(), format='json')

        assert response.status_code == 404
        assert PlanningPlan.objects.count() == 1
        assert PlanningPlanVersion.all_objects.filter(pk__in=[version.pk, draft.pk]).count() == 2

    def test_a_shared_plan_gives_viewers_no_access(
        self, api_client: APIClient, plan: PlanningPlan, url: Callable[..., str], method: str
    ) -> None:
        version = plan_version_service.record_created(plan)
        baker.make('planning.PlanningShared', user=plan.user, plan=plan)
        client = api_client.as_user(baker.make(User))  # ty:ignore[unresolved-attribute]

        assert getattr(client, method)(url(plan, version), payload(), format='json').status_code == 404
