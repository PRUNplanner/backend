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
from planning.models import PlanningCX, PlanningEmpire, PlanningEmpirePlan, PlanningPlan, PlanningShared
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

    @pytest.mark.parametrize('model', [PlanningPlan, PlanningEmpire, PlanningCX, PlanningShared, PlanningEmpirePlan])
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
