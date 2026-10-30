from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from core.admin_charts import Summary
from django.db import connection
from django.db.models import Model
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from model_bakery import baker
from planning.models import (
    PlanningCX,
    PlanningEmpire,
    PlanningEmpirePlan,
    PlanningPlan,
    PlanningPlanVersion,
    PlanningShared,
)
from planning.services import plan_version_service
from user.models import User

JSON_COLUMNS = ('"plan_data"', '"empire_state"', '"cx_data"')


def url(model: type[Model], view: str = 'changelist', *args: object) -> str:
    return reverse(f'admin:planning_{model._meta.model_name}_{view}', args=args)


@dataclass
class Seeded:
    user: User
    cx: PlanningCX
    plans: list[PlanningPlan]
    empire: PlanningEmpire
    share: PlanningShared


@pytest.fixture
def seeded() -> Seeded:
    user = baker.make(User, username='owner')
    cx = baker.make(PlanningCX, user=user, cx_name='My CX')
    plans = baker.make(
        PlanningPlan, user=user, plan_permits_used=1, plan_cogc='AGRICULTURE', planet_natural_id='OT-580b', _quantity=3
    )
    empire = baker.make(PlanningEmpire, user=user, cx=cx, empire_permits_used=1, empire_permits_total=2)
    for plan in plans:
        baker.make(PlanningEmpirePlan, user=user, empire=empire, plan=plan)
    share = baker.make(PlanningShared, user=user, plan=plans[0], view_count=42)
    return Seeded(user=user, cx=cx, plans=plans, empire=empire, share=share)


@pytest.mark.django_db
class TestNoJsonInChangelists:
    """AC10: plan/empire/CX/shared changelists (strip included) select no JSON column."""

    @pytest.mark.parametrize(
        'model',
        [PlanningPlan, PlanningEmpire, PlanningCX, PlanningShared, PlanningEmpirePlan, PlanningPlanVersion],
    )
    def test_changelist_sql(self, admin_client: Client, seeded: Seeded, model: type[Model]) -> None:
        with CaptureQueriesContext(connection) as queries:
            assert admin_client.get(url(model)).status_code == 200

        offending = [q['sql'] for q in queries if any(column in q['sql'] for column in JSON_COLUMNS)]
        assert offending == []


@pytest.mark.django_db
class TestAutocomplete:
    """AC10: change forms use autocomplete for user/plan/empire/cx."""

    @pytest.mark.parametrize(
        'model, fields',
        [
            (PlanningPlan, ['user']),
            (PlanningEmpire, ['user', 'cx']),
            (PlanningCX, ['user']),
            (PlanningShared, ['user', 'plan']),
            (PlanningEmpirePlan, ['user', 'empire', 'plan']),
        ],
    )
    def test_fields_use_autocomplete(self, admin_client: Client, model: type[Model], fields: list[str]) -> None:
        html = admin_client.get(url(model, 'add')).content.decode()

        for field in fields:
            assert f'data-field-name="{field}"' in html
        assert 'admin-autocomplete' in html

    def test_inlines_use_autocomplete(self, admin_client: Client, seeded: Seeded) -> None:
        empire_page = admin_client.get(url(PlanningEmpire, 'change', seeded.empire.pk)).content.decode()
        cx_page = admin_client.get(url(PlanningCX, 'change', seeded.cx.pk)).content.decode()

        assert 'data-field-name="plan"' in empire_page
        assert 'data-field-name="user"' in cx_page
        # the CX inline never renders the empires' JSON state
        assert 'empire_state' not in cx_page

    def test_empire_config_version_is_read_only(self, admin_client: Client, seeded: Seeded) -> None:
        # only the API's configuration saves move the save-conflict version
        html = admin_client.get(url(PlanningEmpire, 'change', seeded.empire.pk)).content.decode()

        assert 'name="config_modified_at' not in html

    def test_autocomplete_endpoint_skips_json(self, admin_client: Client, seeded: Seeded) -> None:
        with CaptureQueriesContext(connection) as queries:
            response = admin_client.get(
                reverse('admin:autocomplete'),
                {'app_label': 'planning', 'model_name': 'planningempireplan', 'field_name': 'plan', 'term': ''},
            )

        assert response.status_code == 200
        assert not any('"plan_data"' in q['sql'] for q in queries)


@pytest.mark.django_db
class TestStrips:
    """AC13: tile values match the underlying querysets."""

    def _summary(self, admin_client: Client, model: type[Model]) -> Summary:
        return admin_client.get(url(model)).context['cl'].model_admin.get_summary(MagicMock())

    def test_plans(self, admin_client: Client, seeded: Seeded) -> None:
        summary = self._summary(admin_client, PlanningPlan)
        tiles = {tile['label']: tile['value'] for tile in summary['tiles']}

        assert tiles == {'Plans': '3', 'Created in 7 d': '3', 'Shared': '33.3%'}
        assert [chart['title'] for chart in summary['charts']] == [
            'New plans per day, last 90 days',
            'Plans by COGC program',
        ]

    def test_empires(self, admin_client: Client, seeded: Seeded) -> None:
        tiles = {tile['label']: tile['value'] for tile in self._summary(admin_client, PlanningEmpire)['tiles']}

        assert tiles == {'Empires': '1', 'Needs state sync': '0', 'Plans per empire': '3.0'}

    def test_shared(self, admin_client: Client, seeded: Seeded) -> None:
        tiles = {tile['label']: tile for tile in self._summary(admin_client, PlanningShared)['tiles']}

        assert tiles['Shares']['value'] == '1'
        assert tiles['Total views']['value'] == '42'
        assert tiles['Top plan']['value'] == seeded.plans[0].plan_name

    def test_strip_renders_on_the_page(self, admin_client: Client, seeded: Seeded) -> None:
        html = admin_client.get(url(PlanningPlan)).content.decode()

        assert 'New plans per day, last 90 days' in html
        assert 'data-type="bar"' in html


@pytest.mark.django_db
class TestHeadersAndLinks:
    def test_plan_header_links(self, admin_client: Client, seeded: Seeded) -> None:
        plan = seeded.plans[0]
        html = admin_client.get(url(PlanningPlan, 'change', plan.pk)).content.decode()

        assert f'plans={plan.pk}' in html
        assert '42 views' in html

    def test_header_links_are_allowed_lookups(self, admin_client: Client, seeded: Seeded) -> None:
        plan, empire = seeded.plans[0], seeded.empire

        in_empires = admin_client.get(url(PlanningEmpire), {'plans': str(plan.pk)})
        in_plan_list = admin_client.get(url(PlanningPlan), {'empires': str(empire.pk)})
        by_owner = admin_client.get(url(PlanningPlan), {'user__id__exact': seeded.user.pk})
        by_planet = admin_client.get(url(PlanningPlan), {'planet_natural_id': 'OT-580b'})

        assert [r.status_code for r in (in_empires, in_plan_list, by_owner, by_planet)] == [200] * 4
        assert len(in_empires.context['cl'].result_list) == 1
        assert len(in_plan_list.context['cl'].result_list) == 3
        assert len(by_planet.context['cl'].result_list) == 3

    def test_view_on_site_needs_frontend_url(self, seeded: Seeded) -> None:
        from django.contrib import admin

        share = seeded.share
        model_admin = admin.site._registry[PlanningShared]

        assert model_admin.get_view_on_site_url(share) is None
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr('planning.admin.settings.frontend_url', 'https://prunplanner.org/')
            assert model_admin.get_view_on_site_url(share) == f'https://prunplanner.org/shared/{share.uuid}'


@pytest.mark.django_db
class TestPlanVersions:
    """Versions on the plan page, restore, and that a plan stays editable and deletable with them."""

    @pytest.fixture
    def plan(self, seeded: Seeded) -> PlanningPlan:
        plan = seeded.plans[0]
        PlanningPlan.objects.filter(pk=plan.pk).update(plan_name='Current', plan_data={'state': 'current'})
        plan.refresh_from_db()
        return plan

    def _draft(self, plan: PlanningPlan) -> PlanningPlanVersion:
        fields = {field: getattr(plan, field) for field in plan_version_service.SNAPSHOT_FIELDS}
        return plan_version_service.save_draft(plan, fields, plan.modified_at)

    def _form_data(self, plan: PlanningPlan, rows: list[PlanningPlanVersion]) -> dict[str, object]:
        # what the browser posts back: the read-only inline's rows too, the draft among them
        data = {
            'user': plan.user_id,  # ty: ignore[unresolved-attribute]
            'plan_name': 'Edited',
            'planet_natural_id': plan.planet_natural_id,
            'plan_permits_used': plan.plan_permits_used,
            'plan_cogc': plan.plan_cogc,
            'plan_data': '{"state": "edited"}',
            'schema_version': plan.schema_version,
            'versions-TOTAL_FORMS': len(rows),
            'versions-INITIAL_FORMS': len(rows),
            'versions-MIN_NUM_FORMS': 0,
            'versions-MAX_NUM_FORMS': 0,
        }
        for index, version in enumerate(rows):
            data[f'versions-{index}-uuid'] = version.pk
            data[f'versions-{index}-plan'] = plan.pk
        return data

    def test_page_lists_versions_with_the_draft_marked(self, admin_client: Client, plan: PlanningPlan) -> None:
        version = plan_version_service.record_created(plan)
        draft = self._draft(plan)

        with CaptureQueriesContext(connection) as queries:
            html = admin_client.get(url(PlanningPlan, 'change', plan.pk)).content.decode()

        assert url(PlanningPlanVersion, 'action_restore', version.pk) in html
        assert url(PlanningPlanVersion, 'action_restore', draft.pk) not in html
        assert 'Draft' in html
        assert not any('"plan_data"' in q['sql'] and 'plan_versions' in q['sql'] for q in queries)

    def test_save_records_an_admin_version(self, admin_client: Client, plan: PlanningPlan) -> None:
        response = admin_client.post(url(PlanningPlan, 'change', plan.pk), self._form_data(plan, []))

        assert response.status_code == 302
        plan.refresh_from_db()
        assert plan.plan_name == 'Edited'
        newest, baseline = PlanningPlanVersion.objects.filter(plan=plan).order_by('-created_at')
        assert (newest.kind, newest.plan_name) == ('admin', 'Edited')
        assert (baseline.kind, baseline.plan_name, baseline.plan_data) == (
            'baseline',
            'Current',
            {'state': 'current'},
        )

    def test_save_with_versions_and_a_draft_on_the_page(self, admin_client: Client, plan: PlanningPlan) -> None:
        version = plan_version_service.record_created(plan)
        draft = self._draft(plan)

        response = admin_client.post(url(PlanningPlan, 'change', plan.pk), self._form_data(plan, [draft, version]))

        assert response.status_code == 302
        plan.refresh_from_db()
        assert plan.plan_name == 'Edited'
        assert PlanningPlanVersion.drafts.filter(plan=plan).exists()

    def test_add_records_a_save_version(self, admin_client: Client, plan: PlanningPlan) -> None:
        response = admin_client.post(url(PlanningPlan, 'add'), self._form_data(plan, []))

        assert response.status_code == 302
        new_plan = PlanningPlan.objects.exclude(pk=plan.pk).get(plan_name='Edited')
        assert [v.kind for v in PlanningPlanVersion.objects.filter(plan=new_plan)] == ['save']

    def test_restore_asks_first(self, admin_client: Client, plan: PlanningPlan) -> None:
        version = plan_version_service.record_created(plan)
        target = url(PlanningPlanVersion, 'action_restore', version.pk)

        response = admin_client.get(target)

        assert response.status_code == 200
        assert 'Restore this version' in response.content.decode()
        assert PlanningPlanVersion.objects.filter(plan=plan).count() == 1

    def test_restore_runs_on_post(self, admin_client: Client, plan: PlanningPlan) -> None:
        version = plan_version_service.record_created(plan)
        PlanningPlan.objects.filter(pk=plan.pk).update(plan_name='Later')

        response = admin_client.post(url(PlanningPlanVersion, 'action_restore', version.pk))

        assert response.status_code == 302
        plan.refresh_from_db()
        assert plan.plan_name == 'Current'
        newest = PlanningPlanVersion.objects.filter(plan=plan).order_by('-created_at')[0]
        assert (newest.kind, newest.source_version) == ('restore', version)

    def test_restore_of_a_draft_is_refused(self, admin_client: Client, plan: PlanningPlan) -> None:
        draft = self._draft(plan)

        admin_client.post(url(PlanningPlanVersion, 'action_restore', draft.pk))

        plan.refresh_from_db()
        assert plan.plan_name == 'Current'

    def test_restore_needs_the_change_permission(self, client: Client, plan: PlanningPlan) -> None:
        version = plan_version_service.record_created(plan)
        client.force_login(baker.make(User, is_staff=True))

        response = client.post(url(PlanningPlanVersion, 'action_restore', version.pk))

        assert response.status_code == 403
        assert PlanningPlanVersion.objects.filter(plan=plan).count() == 1

    def test_versions_are_read_only(self, admin_client: Client, plan: PlanningPlan) -> None:
        version = plan_version_service.record_created(plan)

        assert admin_client.get(url(PlanningPlanVersion, 'add')).status_code == 403
        assert 'name="name"' not in admin_client.get(url(PlanningPlanVersion, 'change', version.pk)).content.decode()
        assert 'delete_selected' not in admin_client.get(url(PlanningPlanVersion)).content.decode()

    def test_a_plan_with_versions_can_be_deleted(self, admin_client: Client, plan: PlanningPlan) -> None:
        plan_version_service.record_created(plan)
        target = url(PlanningPlan, 'delete', plan.pk)

        assert 'perms_lacking' not in admin_client.get(target).content.decode()
        assert admin_client.get(target).context['perms_lacking'] == set()

        assert admin_client.post(target, {'post': 'yes'}).status_code == 302
        assert not PlanningPlan.objects.filter(pk=plan.pk).exists()
        assert PlanningPlanVersion.all_objects.filter(plan_id=plan.pk).count() == 0
