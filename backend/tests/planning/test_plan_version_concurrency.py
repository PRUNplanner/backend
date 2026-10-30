from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connection
from django.urls import reverse
from model_bakery import baker
from planning.models import PlanningPlan, PlanningPlanVersion
from planning.services import plan_version_service
from rest_framework.response import Response
from rest_framework.test import APIClient
from tests.fixtures.planning.fxt_plan_vallis import plan_data_vallis
from user.models import User

pytestmark = pytest.mark.django_db(transaction=True)

WORKERS = 6
ROUNDS = range(3)
SNAPSHOT = plan_version_service.SNAPSHOT_FIELDS


@pytest.fixture(autouse=True)
def postgres_only() -> None:
    if connection.vendor != 'postgresql':
        pytest.skip('needs real row locks; CI runs these on Postgres')


@pytest.fixture
def owner() -> User:
    return baker.make(User)


@pytest.fixture
def plan(owner: User) -> PlanningPlan:
    return baker.make(PlanningPlan, user=owner, plan_name='Base', plan_permits_used=1, plan_cogc='---', plan_data={})


def together(*calls: Callable[[], Response]) -> list[int]:
    """Runs the calls at the same moment, each on its own connection; an unhandled error fails the test."""
    barrier = Barrier(len(calls))

    def run(call: Callable[[], Response]) -> int:
        barrier.wait(timeout=10)
        try:
            return call().status_code
        finally:
            connection.close()

    with ThreadPoolExecutor(len(calls)) as pool:
        return [future.result(timeout=30) for future in [pool.submit(run, call) for call in calls]]


def client_for(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


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


def detail(plan: PlanningPlan) -> str:
    return reverse('planning:plan-detail', kwargs={'pk': plan.pk})


def draft_url(plan: PlanningPlan) -> str:
    return reverse('planning:plan-draft', kwargs={'plan_pk': plan.pk})


def put(plan: PlanningPlan, **overrides: object) -> Callable[[], Response]:
    return lambda: client_for(plan.user).put(detail(plan), payload(**overrides), format='json')


def put_draft(plan: PlanningPlan, **overrides: object) -> Callable[[], Response]:
    body = payload(base_modified_at=plan.modified_at.isoformat(), **overrides)
    return lambda: client_for(plan.user).put(draft_url(plan), body, format='json')


def delete_draft(plan: PlanningPlan) -> Callable[[], Response]:
    return lambda: client_for(plan.user).delete(draft_url(plan))


def saved(plan: PlanningPlan) -> list[PlanningPlanVersion]:
    return list(PlanningPlanVersion.objects.filter(plan=plan).order_by('-created_at'))


def assert_row_is_newest_version(plan: PlanningPlan) -> None:
    row = PlanningPlan.objects.get(pk=plan.pk)
    newest = saved(plan)[0]
    assert {field: getattr(newest, field) for field in SNAPSHOT} == {field: getattr(row, field) for field in SNAPSHOT}


def with_full_history(plan: PlanningPlan) -> PlanningPlanVersion:
    """Saves until the prune is one step away from the returned, oldest version."""
    client = client_for(plan.user)
    for index in range(10):
        assert client.put(detail(plan), payload(plan_name=f'Old {index}'), format='json').status_code == 200
    return saved(plan)[-1]


@pytest.mark.parametrize('_round', ROUNDS)
def test_saves_keep_every_version_in_row_order(plan: PlanningPlan, _round: int) -> None:
    calls = [put(plan, plan_name=f'Save {index}', version_name=f'v{index}') for index in range(WORKERS)]

    assert together(*calls) == [200] * WORKERS

    versions = saved(plan)
    assert sorted(version.name for version in versions if version.kind == 'save') == [
        f'v{index}' for index in range(WORKERS)
    ]
    baselines = [version for version in versions if version.kind == 'baseline']
    assert [version.plan_name for version in baselines] == ['Base']
    assert_row_is_newest_version(plan)


@pytest.mark.parametrize('_round', ROUNDS)
def test_saves_from_one_base_let_one_through(plan: PlanningPlan, _round: int) -> None:
    base = plan.modified_at.isoformat()
    calls = [put(plan, plan_name=f'Save {index}', base_modified_at=base) for index in range(WORKERS)]

    codes = together(*calls)

    assert sorted(codes) == [200] + [409] * (WORKERS - 1)
    winner = f'Save {codes.index(200)}'
    assert PlanningPlan.objects.get(pk=plan.pk).plan_name == winner
    assert [version.plan_name for version in saved(plan)] == [winner, 'Base']


@pytest.mark.parametrize('_round', ROUNDS)
def test_drafts_end_in_one_draft_and_leave_the_plan_alone(plan: PlanningPlan, _round: int) -> None:
    calls = [put_draft(plan, plan_name=f'Draft {index}') for index in range(WORKERS)]

    assert together(*calls) == [200] * WORKERS

    assert PlanningPlanVersion.drafts.filter(plan=plan).count() == 1
    row = PlanningPlan.objects.get(pk=plan.pk)
    assert (row.plan_name, row.modified_at) == ('Base', plan.modified_at)


@pytest.mark.parametrize('_round', ROUNDS)
def test_saves_drafts_and_draft_deletes_mixed(plan: PlanningPlan, _round: int) -> None:
    codes = together(
        put(plan, plan_name='Save 0'),
        put_draft(plan, plan_name='Draft 0'),
        delete_draft(plan),
        put(plan, plan_name='Save 1'),
        put_draft(plan, plan_name='Draft 1'),
        delete_draft(plan),
        put(plan, plan_name='Save 2'),
    )

    assert sorted(codes) == [200] * 5 + [204] * 2
    assert PlanningPlanVersion.drafts.filter(plan=plan).count() <= 1
    assert len(saved(plan)) == 4
    assert_row_is_newest_version(plan)


@pytest.mark.parametrize('_round', ROUNDS)
def test_restoring_a_version_the_prune_is_removing(plan: PlanningPlan, _round: int) -> None:
    oldest = with_full_history(plan)

    codes = together(
        put(plan, plan_name='Save 0'),
        put(plan, plan_name='Restored', restored_from=str(oldest.pk)),
        put(plan, plan_name='Save 1'),
    )

    assert codes[0] == codes[2] == 200
    assert codes[1] in (200, 400)
    assert len(saved(plan)) == 10
    assert_row_is_newest_version(plan)


@pytest.mark.parametrize('_round', ROUNDS)
def test_renaming_a_version_the_prune_is_removing(plan: PlanningPlan, _round: int) -> None:
    oldest = with_full_history(plan)
    url = reverse('planning:plan-version-detail', kwargs={'plan_pk': plan.pk, 'pk': oldest.pk})

    codes = together(
        put(plan, plan_name='Save 0'),
        lambda: client_for(plan.user).patch(url, {'name': 'Keep me'}, format='json'),
        put(plan, plan_name='Save 1'),
    )

    assert codes[0] == codes[2] == 200
    assert codes[1] in (200, 404)
    assert PlanningPlanVersion.objects.filter(pk=oldest.pk, name='Keep me').exists() == (codes[1] == 200)
    assert_row_is_newest_version(plan)


@pytest.mark.parametrize('_round', ROUNDS)
def test_cloning_a_version_the_prune_is_removing(plan: PlanningPlan, _round: int) -> None:
    oldest = with_full_history(plan)
    url = reverse('planning:plan-version-clone', kwargs={'plan_pk': plan.pk, 'pk': oldest.pk})

    codes = together(
        put(plan, plan_name='Save 0'),
        lambda: client_for(plan.user).post(url),
        put(plan, plan_name='Save 1'),
    )

    assert codes[0] == codes[2] == 200
    assert codes[1] in (201, 404)
    assert_row_is_newest_version(plan)


@pytest.mark.parametrize('_round', ROUNDS)
def test_admin_restore_during_saves(plan: PlanningPlan, _round: int) -> None:
    with_full_history(plan)

    def admin_restore(version: PlanningPlanVersion) -> Callable[[], Response]:
        def run() -> Response:
            plan_version_service.restore_version(version)
            return Response(status=200)

        return run

    restores = [admin_restore(version) for version in saved(plan)[:3]]
    saves = [put(plan, plan_name=f'Save {index}') for index in range(3)]

    assert together(*saves, *restores) == [200] * 6
    assert_row_is_newest_version(plan)


@pytest.mark.parametrize('_round', ROUNDS)
def test_deleting_a_plan_during_saves(plan: PlanningPlan, _round: int) -> None:
    client_for(plan.user).put(detail(plan), payload(), format='json')
    plan_pk = plan.pk

    codes = together(
        put(plan, plan_name='Save 0'),
        lambda: client_for(plan.user).delete(detail(plan)),
        put(plan, plan_name='Save 1'),
        put_draft(plan, plan_name='Draft 0'),
    )

    assert codes[1] == 204
    assert all(code in (200, 404) for code in (codes[0], codes[2], codes[3]))
    assert not PlanningPlan.objects.filter(pk=plan_pk).exists()
    assert not PlanningPlanVersion.all_objects.filter(plan_id=plan_pk).exists()
