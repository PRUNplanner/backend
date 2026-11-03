import pytest
from model_bakery import baker
from planning.models import PlanningEmpirePlan
from planning.services.junction_service import JunctionRejected, sync_cx_empires, sync_empire_plans
from tests.fixtures.planning.fxt_plan_vallis import plan_data_vallis

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return baker.make('user.User')


def _plan(user):
    return baker.make('planning.PlanningPlan', user=user, plan_data=plan_data_vallis)


def test_sync_empire_plans_sets_exact_links(user):
    empire = baker.make('planning.PlanningEmpire', user=user)
    keep, new, drop = _plan(user), _plan(user), _plan(user)
    empire.plans.add(keep, drop, through_defaults={'user': user})

    sync_empire_plans(user, {empire.uuid: {keep.uuid, new.uuid}})

    assert set(PlanningEmpirePlan.objects.filter(empire=empire).values_list('plan_id', flat=True)) == {
        keep.uuid,
        new.uuid,
    }


def test_sync_empire_plans_rejects_foreign_plan_without_writing(user):
    empire = baker.make('planning.PlanningEmpire', user=user)
    foreign = _plan(baker.make('user.User'))

    with pytest.raises(JunctionRejected) as exc:
        sync_empire_plans(user, {empire.uuid: {foreign.uuid}})

    assert exc.value.status_code == 403
    assert exc.value.body['invalid_plans'] == [foreign.uuid]
    assert not PlanningEmpirePlan.objects.exists()


def test_sync_cx_empires_assigns_and_clears(user):
    cx = baker.make('planning.PlanningCX', user=user)
    assigned = baker.make('planning.PlanningEmpire', user=user)
    cleared = baker.make('planning.PlanningEmpire', user=user, cx=cx)

    sync_cx_empires(user, [(cx.uuid, [assigned.uuid])])

    assigned.refresh_from_db()
    cleared.refresh_from_db()
    assert assigned.cx_id == cx.uuid
    assert cleared.cx_id is None


@pytest.mark.parametrize(
    ('foreign', 'duplicate', 'status_code', 'error'),
    [
        ('cx', False, 403, 'Invalid CX UUIDs detected.'),
        ('empire', False, 403, 'Invalid Empire UUIDs detected.'),
        (None, True, 400, 'Duplicate empire assignment in request.'),
    ],
)
def test_sync_cx_empires_rejects(user, foreign, duplicate, status_code, error):
    other = baker.make('user.User')
    cx = baker.make('planning.PlanningCX', user=other if foreign == 'cx' else user)
    empire = baker.make('planning.PlanningEmpire', user=other if foreign == 'empire' else user)

    with pytest.raises(JunctionRejected) as exc:
        sync_cx_empires(user, [(cx.uuid, [empire.uuid, empire.uuid] if duplicate else [empire.uuid])])

    assert (exc.value.status_code, exc.value.body) == (status_code, {'error': error})
