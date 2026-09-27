import re
from typing import cast
from unittest.mock import patch

import pytest
from core.admin import ReadOnlyAdminMixin, environment_callback, log_admin_action
from core.config.settings.unfold import UNFOLD
from django.contrib import admin
from django.contrib.admin.models import CHANGE, LogEntry
from django.db.models import Model
from django.http import HttpRequest
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask
from model_bakery import baker
from unfold.utils import convert_color
from user.models import User, UserAPIKey

STEPS = ('50', '100', '200', '300', '400', '500', '600', '700', '800', '900', '950')

# every model an admin is registered for, in a stable order for test ids
REGISTERED = sorted(admin.site._registry.items(), key=lambda item: item[0]._meta.label_lower)

READ_ONLY_MODELS = {
    'analytics.appstatistic',
    'analytics.analyticsplanaggregate',
    'analytics.analyticsempirematerialsnapshot',
    'gamedata.gameexchangecxpc',
    'gamedata.gameexchangeanalytics',
    'gamedata.gameexchange',
    'gamedata.gamematerial',
    'gamedata.gamebuilding',
    'gamedata.gamerecipe',
    'gamedata.gameplanet',
    'gamedata.gamefioplayerdata',
    'user.verificationcode',
}


def bake(model: type[Model], superuser: User) -> Model:
    """One saved row of any registered model, with the few relations the defaults can't fill."""
    if model is PeriodicTask:
        return baker.make(PeriodicTask, interval=baker.make(IntervalSchedule, every=1, period='hours'))
    if model is CrontabSchedule:
        return baker.make(CrontabSchedule, timezone='UTC')
    if model is LogEntry:
        return baker.make(LogEntry, user=superuser, action_flag=CHANGE, object_repr='x')
    if model is User:
        return superuser
    if model is UserAPIKey:
        return baker.make(UserAPIKey, name='key', user=superuser)
    return baker.make(model)


def request_as(user: User) -> HttpRequest:
    request = RequestFactory().get('/')
    request.user = user
    return request


def admin_url(model: type[Model], view: str, *args: object) -> str:
    return reverse(f'admin:{model._meta.app_label}_{model._meta.model_name}_{view}', args=args)


@pytest.mark.django_db
class TestAdminSmoke:
    """AC1: every registered admin's changelist, search, add and change pages render for a superuser."""

    @pytest.mark.parametrize('model, model_admin', REGISTERED, ids=[m._meta.label_lower for m, _ in REGISTERED])
    def test_pages(self, admin_client: Client, superuser: User, model: type[Model], model_admin: admin.ModelAdmin):
        obj = bake(model, superuser)

        assert admin_client.get(admin_url(model, 'changelist')).status_code == 200
        if model_admin.search_fields:
            assert admin_client.get(admin_url(model, 'changelist'), {'q': 'test'}).status_code == 200
        if model_admin.has_add_permission(request_as(superuser)):
            assert admin_client.get(admin_url(model, 'add')).status_code == 200
        assert admin_client.get(admin_url(model, 'change', obj.pk)).status_code == 200


@pytest.mark.django_db
class TestReadOnlyModels:
    """AC5: FIO-sourced and derived models show no add/change controls."""

    @pytest.mark.parametrize('label', sorted(READ_ONLY_MODELS))
    def test_no_add_or_change(self, superuser: User, label: str) -> None:
        model_admin = next(ma for m, ma in REGISTERED if m._meta.label_lower == label)
        request = request_as(superuser)

        assert isinstance(model_admin, ReadOnlyAdminMixin)
        assert not model_admin.has_add_permission(request)
        assert not model_admin.has_change_permission(request)

    @pytest.mark.parametrize('label', ['gamedata.gameexchangecxpc', 'gamedata.gameexchangeanalytics'])
    def test_market_tables_have_no_delete(self, superuser: User, label: str) -> None:
        model_admin = next(ma for m, ma in REGISTERED if m._meta.label_lower == label)

        assert not model_admin.has_delete_permission(request_as(superuser))
        assert 'delete_selected' not in model_admin.get_actions(request_as(superuser))

    def test_changelist_has_no_add_link(self, admin_client: Client) -> None:
        from gamedata.models import GameMaterial

        response = admin_client.get(admin_url(GameMaterial, 'changelist'))

        assert admin_url(GameMaterial, 'add') not in response.content.decode()


@pytest.mark.django_db
class TestAudit:
    def test_log_entries_are_immutable(self, superuser: User) -> None:
        """AC2: no add, change or delete of the audit trail."""
        model_admin = admin.site._registry[LogEntry]
        request = request_as(superuser)
        entry = baker.make(LogEntry, user=superuser, action_flag=CHANGE)

        assert not model_admin.has_add_permission(request)
        assert not model_admin.has_change_permission(request, entry)
        assert not model_admin.has_delete_permission(request, entry)
        assert 'delete_selected' not in model_admin.get_actions(request)

    def test_log_admin_action_on_object_and_model(self, superuser: User) -> None:
        request = request_as(superuser)

        on_object = log_admin_action(request, superuser, 'Did a thing')
        on_model = log_admin_action(request, User, 'Did it to many')

        assert (on_object.object_id, on_object.change_message) == (str(superuser.pk), 'Did a thing')
        assert on_model.object_id is None
        assert on_model.object_repr == 'Users'
        assert LogEntry.objects.count() == 2


@pytest.mark.django_db
class TestSite:
    def test_environment_badge_local(self, admin_client: Client) -> None:
        assert environment_callback(RequestFactory().get('/')) == ['Local', 'success']

    def test_environment_badge_production(self, admin_client: Client) -> None:
        """AC6"""
        from core.config.django import production

        assert production.ENVIRONMENT_NAME == 'production'
        with override_settings(ENVIRONMENT_NAME=production.ENVIRONMENT_NAME):
            assert environment_callback(RequestFactory().get('/')) == ['Production', 'danger']
            html = admin_client.get(reverse('admin:user_user_changelist')).content.decode()

        assert 'Production' in html
        assert '<title>[Production]' in html

    def test_theme_sets_every_step(self, admin_client: Client) -> None:
        """AC7: every base and primary step comes from UNFOLD['COLORS'], none from Unfold's defaults."""
        html = admin_client.get(reverse('admin:user_user_changelist')).content.decode()

        colours = cast(dict[str, dict[str, str]], UNFOLD['COLORS'])
        for name in ('base', 'primary'):
            ramp = colours[name]
            assert set(ramp) == set(STEPS)
            for step in STEPS:
                assert f'--color-{name}-{step}: {convert_color(ramp[step])};' in html

    def test_admin_url_from_settings(self) -> None:
        assert reverse('admin:index') == '/admin/'

    def test_sidebar_badge_counts_stuck_planets_and_hides_at_zero(self, admin_client: Client) -> None:
        url = reverse('admin:user_user_changelist')
        assert 'data-sidebar-badge' not in admin_client.get(url).content.decode()

        baker.make('gamedata.GamePlanet', automation_refresh_status='pending', automation_next_retry_at=None)
        html = admin_client.get(url).content.decode()

        assert re.search(r'data-sidebar-badge[^>]*>1</span>', html)
        assert html.count('data-sidebar-badge') == 1
        assert reverse('admin:task_health') in html


@pytest.mark.django_db
class TestSummaryStrip:
    def test_failing_summary_hides_the_strip(self, admin_client: Client) -> None:
        """AC13: a summary that raises hides the strip, the page still renders."""
        from planning.admin import PlanningPlanAdmin

        url = reverse('admin:planning_planningplan_changelist')
        assert 'Created in 7 d' in admin_client.get(url).content.decode()

        with patch.object(PlanningPlanAdmin, 'get_summary', side_effect=RuntimeError('boom')):
            response = admin_client.get(url)

        assert response.status_code == 200
        assert 'Created in 7 d' not in response.content.decode()

    def test_cache_outage_still_shows_the_strip(self, admin_client: Client) -> None:
        with patch('core.admin.cache.get', side_effect=ConnectionError('redis down')):
            response = admin_client.get(reverse('admin:planning_planningplan_changelist'))

        assert response.status_code == 200
        assert 'Created in 7 d' in response.content.decode()

    def test_summary_is_cached(self, admin_client: Client, locmem_cache) -> None:
        from planning.admin import PlanningPlanAdmin

        url = reverse('admin:planning_planningplan_changelist')
        with patch.object(PlanningPlanAdmin, 'get_summary', wraps=PlanningPlanAdmin.get_summary, autospec=True) as spy:
            admin_client.get(url)
            admin_client.get(url)

        assert spy.call_count == 1


@pytest.mark.django_db
class TestTaskHealthPage:
    def test_renders_and_filters_by_state(self, admin_client: Client) -> None:
        interval = baker.make(IntervalSchedule, every=1, period='hours')
        baker.make(PeriodicTask, name='paused-one', task='x', interval=interval, enabled=False)
        baker.make(PeriodicTask, name='running-one', task='y', interval=interval, enabled=True)
        url = reverse('admin:task_health')

        everything = admin_client.get(url).content.decode()
        paused = admin_client.get(url, {'state': 'paused'}).content.decode()

        assert 'paused-one' in everything and 'running-one' in everything
        assert 'paused-one' in paused and 'running-one' not in paused
        # Redis isn't reachable under the test settings: the page says so instead of failing
        assert 'Redis unavailable' in everything

    def test_periodic_task_list_links_to_task_health(self, admin_client: Client) -> None:
        html = admin_client.get(reverse('admin:django_celery_beat_periodictask_changelist')).content.decode()

        assert reverse('admin:task_health') in html
