from collections.abc import Callable
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from model_bakery import baker
from planning.models import PlanningPlan, PlanningPlanVersion, PlanVersionKind
from planning.services import plan_version_service
from structlog.testing import capture_logs
from tests.fixtures.planning.fxt_plan_vallis import plan_data_vallis
from user.models import User

pytestmark = pytest.mark.django_db

SNAPSHOT = plan_version_service.SNAPSHOT_FIELDS
NOW = timezone.now()


def snapshot(obj: PlanningPlan | PlanningPlanVersion) -> dict[str, object]:
    return {field: getattr(obj, field) for field in SNAPSHOT}


@pytest.fixture
def plan() -> PlanningPlan:
    return baker.make(
        PlanningPlan,
        user=baker.make(User),
        plan_name='Base',
        plan_permits_used=1,
        plan_cogc='---',
        planet_natural_id='OT-580b',
        plan_data=plan_data_vallis,
    )


def save(plan: PlanningPlan, name: str = '', restored_from: PlanningPlanVersion | None = None) -> PlanningPlanVersion:
    # a save as the serializer does it: the row before, then the update, then the version
    before = PlanningPlan.objects.get(pk=plan.pk)
    plan.plan_permits_used = 1 + (plan.plan_permits_used % 3)
    plan.save()
    return plan_version_service.record_save(plan, before=before, name=name, restored_from=restored_from)


def versions(plan: PlanningPlan) -> list[PlanningPlanVersion]:
    return list(PlanningPlanVersion.objects.filter(plan=plan).order_by('-created_at'))


def seed(plan: PlanningPlan, count: int, name: Callable[[int], str] = lambda _: '') -> list[PlanningPlanVersion]:
    return [plan_version_service._insert(plan, PlanVersionKind.SAVE, name=name(i)) for i in range(count)]


class TestConstraints:
    def test_one_draft_per_plan(self, plan: PlanningPlan) -> None:
        plan_version_service.save_draft(plan, snapshot(plan), NOW)

        with pytest.raises(IntegrityError), transaction.atomic():
            baker.make(PlanningPlanVersion, plan=plan, user=plan.user, kind='draft', data_bytes=1, base_modified_at=NOW)

    @pytest.mark.parametrize(
        'fields',
        [
            {'kind': 'draft'},  # no base
            {'kind': 'save', 'base_modified_at': NOW},
            {'kind': 'draft', 'base_modified_at': NOW, 'name': 'Named'},
        ],
    )
    def test_draft_shape(self, plan: PlanningPlan, fields: dict[str, object]) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            PlanningPlanVersion.all_objects.create(
                plan=plan, user=plan.user, data_bytes=1, plan_permits_used=1, **fields
            )


class TestManagers:
    def test_objects_and_plan_cascade(self, plan: PlanningPlan) -> None:
        version = save(plan)
        plan_version_service.save_draft(plan, snapshot(plan), NOW)

        assert version in PlanningPlanVersion.objects.all()
        assert PlanningPlanVersion.objects.filter(kind='draft').count() == 0
        assert PlanningPlanVersion.drafts.count() == 1
        assert PlanningPlanVersion.all_objects.count() == 3  # baseline, save, draft

        plan.delete()

        assert PlanningPlanVersion.all_objects.count() == 0


class TestRecordSave:
    def test_first_save_keeps_the_state_before_as_baseline(self, plan: PlanningPlan) -> None:
        before = snapshot(plan)

        save(plan)

        newest, baseline = versions(plan)
        assert (newest.kind, baseline.kind) == ('save', 'baseline')
        assert snapshot(baseline) == before
        assert snapshot(newest) == snapshot(plan)

    def test_later_saves_add_one_version(self, plan: PlanningPlan) -> None:
        save(plan)

        save(plan)

        assert [v.kind for v in versions(plan)] == ['save', 'save', 'baseline']

    def test_name_and_restore(self, plan: PlanningPlan) -> None:
        source = save(plan, name='Early')

        restored = save(plan, restored_from=source)

        assert source.name == 'Early'
        assert (restored.kind, restored.source_version) == ('restore', source)

    def test_stores_size_and_owner(self, plan: PlanningPlan) -> None:
        version = save(plan)

        assert version.user == plan.user
        assert version.data_bytes == len(plan_version_service.orjson.dumps(plan.plan_data))

    def test_deletes_the_draft(self, plan: PlanningPlan) -> None:
        plan_version_service.save_draft(plan, snapshot(plan), NOW)

        save(plan)

        assert PlanningPlanVersion.drafts.filter(plan=plan).count() == 0

    def test_a_users_restore_deletes_the_draft(self, plan: PlanningPlan) -> None:
        source = save(plan)
        plan_version_service.save_draft(plan, snapshot(plan), NOW)

        save(plan, restored_from=source)

        assert PlanningPlanVersion.drafts.filter(plan=plan).count() == 0

    def test_keep_draft_leaves_the_draft(self, plan: PlanningPlan) -> None:
        plan_version_service.save_draft(plan, snapshot(plan), NOW)
        before = PlanningPlan.objects.get(pk=plan.pk)

        plan_version_service.record_save(plan, before=before, kind=PlanVersionKind.ADMIN, keep_draft=True)

        assert PlanningPlanVersion.drafts.filter(plan=plan).count() == 1

    def test_logs_one_line_per_version(self, plan: PlanningPlan) -> None:
        with capture_logs() as logs:
            version = save(plan, name='Early')

        created = [line for line in logs if line['event'] == 'plan_version_created']
        assert [(line['kind'], line['named']) for line in created] == [('baseline', False), ('save', True)]
        assert created[1]['plan_uuid'] == str(plan.pk)
        assert created[1]['data_bytes'] == version.data_bytes
        assert created[1]['pruned'] == 0


class TestPrune:
    def test_keeps_the_newest_10_unnamed_and_every_named(self, plan: PlanningPlan) -> None:
        named = seed(plan, 3, lambda i: f'Named {i}')
        unnamed = seed(plan, 12)

        with capture_logs() as logs:
            newest = save(plan)

        kept = versions(plan)
        assert {v.pk for v in named} <= {v.pk for v in kept}
        assert [v.pk for v in kept if not v.name] == [newest.pk, *[v.pk for v in reversed(unnamed)][:9]]
        assert logs[-1]['pruned'] == 3

    def test_blank_name_makes_a_version_prunable(self, plan: PlanningPlan) -> None:
        oldest = seed(plan, 1, lambda _: 'Keep')[0]
        seed(plan, 9)

        save(plan)
        assert PlanningPlanVersion.objects.filter(pk=oldest.pk).exists()

        plan_version_service.rename_version(oldest, '')
        save(plan)

        assert not PlanningPlanVersion.objects.filter(pk=oldest.pk).exists()

    def test_clears_source_version_of_restores(self, plan: PlanningPlan) -> None:
        source = seed(plan, 1)[0]
        restored = save(plan, name='Kept', restored_from=source)
        seed(plan, 9)

        save(plan)

        assert not PlanningPlanVersion.objects.filter(pk=source.pk).exists()
        restored.refresh_from_db()
        assert restored.source_version is None

    def test_loads_no_plan_data(self, plan: PlanningPlan) -> None:
        seed(plan, 11)

        with CaptureQueriesContext(connection) as queries:
            plan_version_service._prune(plan)

        assert not any('SELECT' in q['sql'] and '"plan_data"' in q['sql'] for q in queries)
        assert PlanningPlanVersion.objects.filter(plan=plan).count() == 10


def test_record_created_has_no_baseline(plan: PlanningPlan) -> None:
    plan_version_service.record_created(plan)

    assert [v.kind for v in versions(plan)] == ['save']


class TestRename:
    def test_logs(self, plan: PlanningPlan) -> None:
        version = save(plan)

        with capture_logs() as logs:
            plan_version_service.rename_version(version, 'Late')

        version.refresh_from_db()
        assert version.name == 'Late'
        assert logs == [
            {'event': 'plan_version_renamed', 'plan_uuid': str(plan.pk), 'named': True, 'log_level': 'info'}
        ]


class TestClone:
    def test_new_plan_with_the_versions_data(self, plan: PlanningPlan) -> None:
        version = save(plan, name='Early')
        snapshot_of_version = snapshot(version)
        save(plan)
        original = snapshot(PlanningPlan.objects.get(pk=plan.pk))
        original_versions = len(versions(plan))

        with capture_logs() as logs:
            clone = plan_version_service.clone_version(version, plan.user)

        assert clone.pk != plan.pk
        assert clone.plan_name == 'Base (Early)'
        assert {**snapshot(clone), 'plan_name': 'Base'} == snapshot_of_version
        assert [v.kind for v in versions(clone)] == ['save']
        assert snapshot(PlanningPlan.objects.get(pk=plan.pk)) == original
        assert len(versions(plan)) == original_versions
        assert {'event': 'plan_version_cloned', 'plan_uuid': str(plan.pk), 'log_level': 'info'} in logs

    def test_unnamed_version_uses_its_date(self, plan: PlanningPlan) -> None:
        version = save(plan)

        clone = plan_version_service.clone_version(version, plan.user)

        assert clone.plan_name == f'Base ({version.created_at:%Y-%m-%d})'

    def test_longest_names_fit(self, plan: PlanningPlan) -> None:
        PlanningPlan.objects.filter(pk=plan.pk).update(plan_name='x' * 200)
        plan.refresh_from_db()
        version = save(plan, name='y' * 80)

        clone = plan_version_service.clone_version(version, plan.user)

        assert len(clone.plan_name) == 200
        assert clone.plan_name.endswith(f' ({"y" * 80})')


class TestRestore:
    def test_updates_the_plan_through_a_new_version(self, plan: PlanningPlan) -> None:
        source = save(plan)
        save(plan)
        modified_at = PlanningPlan.objects.get(pk=plan.pk).modified_at

        with capture_logs() as logs:
            restored = plan_version_service.restore_version(source)

        plan.refresh_from_db()
        newest = versions(plan)[0]
        assert snapshot(plan) == snapshot(source)
        assert plan.modified_at > modified_at
        assert (newest.kind, newest.source_version) == ('restore', source)
        assert snapshot(newest) == snapshot(restored)
        assert {
            'event': 'plan_version_restored_admin',
            'plan_uuid': str(plan.pk),
            'version_uuid': str(source.pk),
            'log_level': 'info',
        } in logs

    def test_keeps_the_users_draft(self, plan: PlanningPlan) -> None:
        source = save(plan)
        draft = plan_version_service.save_draft(plan, snapshot(plan), NOW)

        plan_version_service.restore_version(source)

        assert PlanningPlanVersion.drafts.get(plan=plan).pk == draft.pk


class TestActivate:
    def test_logs(self, plan: PlanningPlan) -> None:
        source = save(plan, name='Early')
        save(plan)

        with capture_logs() as logs:
            plan_version_service.activate_version(source, None)

        assert versions(plan)[0].source_version == source
        assert {
            'event': 'plan_version_activated',
            'plan_uuid': str(plan.pk),
            'version_uuid': str(source.pk),
            'named': True,
            'log_level': 'info',
        } in logs

    def test_deletes_only_the_draft_it_was_told_about(self, plan: PlanningPlan) -> None:
        source = save(plan)
        draft = plan_version_service.save_draft(plan, snapshot(plan), NOW)

        plan_version_service.activate_version(source, None)
        assert PlanningPlanVersion.drafts.filter(plan=plan).exists()

        plan_version_service.activate_version(source, draft.updated_at)
        assert not PlanningPlanVersion.drafts.filter(plan=plan).exists()


class TestDelete:
    def test_logs(self, plan: PlanningPlan) -> None:
        old = save(plan)
        save(plan)

        with capture_logs() as logs:
            plan_version_service.delete_version(old)

        assert old not in versions(plan)
        assert {
            'event': 'plan_version_deleted',
            'plan_uuid': str(plan.pk),
            'kind': 'save',
            'named': False,
            'log_level': 'info',
        } in logs


class TestDraft:
    def test_second_write_replaces_the_first(self, plan: PlanningPlan) -> None:
        first = plan_version_service.save_draft(plan, snapshot(plan), NOW)
        fields = {**snapshot(plan), 'plan_name': 'Edited'}
        later = NOW + timedelta(minutes=1)

        second = plan_version_service.save_draft(plan, fields, later)

        draft = PlanningPlanVersion.drafts.get(plan=plan)
        assert draft.pk == first.pk == second.pk
        assert (draft.plan_name, draft.base_modified_at) == ('Edited', later)
        assert draft.updated_at > first.created_at

    def test_leaves_the_plan_and_its_caches_alone(self, plan: PlanningPlan) -> None:
        modified_at = PlanningPlan.objects.get(pk=plan.pk).modified_at

        with patch('core.services.cache_manager.CacheManager.invalidate_on_commit') as invalidate:
            plan_version_service.save_draft(plan, snapshot(plan), NOW)
            plan_version_service.delete_draft(plan)

        invalidate.assert_not_called()
        assert PlanningPlan.objects.get(pk=plan.pk).modified_at == modified_at


def test_restoring_a_version_pruned_while_waiting_for_the_lock(plan: PlanningPlan) -> None:
    version = plan_version_service.record_created(plan)
    PlanningPlanVersion.objects.filter(pk=version.pk).delete()

    plan_version_service.restore_version(version)

    newest = PlanningPlanVersion.objects.filter(plan=plan).order_by('-created_at').first()
    assert newest is not None
    assert (newest.kind, newest.source_version_id) == (PlanVersionKind.RESTORE, None)  # ty: ignore[unresolved-attribute]
