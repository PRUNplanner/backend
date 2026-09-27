import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from analytics import dashboard
from analytics.models import AppStatistic
from core.admin_charts import Chart
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django_celery_beat.models import IntervalSchedule, PeriodicTask
from gamedata.models import GamePlanet
from model_bakery import baker
from planning.models import PlanningPlan
from user.models import User

INDEX = reverse('admin:index')


def chart_labels(chart: Chart) -> list[str]:
    return json.loads(chart['config']['data'])['labels']


@pytest.fixture
def stats() -> None:
    today = timezone.now().date()
    for offset in range(400):
        baker.make(
            AppStatistic,
            date=today - timedelta(days=offset),
            user_count=1000 - offset,
            users_active_today=10,
            users_active_7d=40,
            users_active_30d=100,
        )


@pytest.mark.django_db
class TestFaultIsolation:
    """AC14: Redis down and Postgres stats failing still render the index, with "unavailable" cards."""

    def test_redis_and_postgres_down(self, admin_client: Client, stats: None) -> None:
        with (
            patch.object(dashboard, '_redis', side_effect=ConnectionError('redis down')),
            patch.object(dashboard, '_fetch', side_effect=RuntimeError('pg down')),
        ):
            response = admin_client.get(INDEX)

        html = response.content.decode()
        assert response.status_code == 200
        assert response.context['redis'] is None
        assert response.context['postgres'] is None
        assert html.count('unavailable') >= 2
        # the rest of the page still works
        assert response.context['headline'] is not None
        assert 'Total users, last 30 days' in html

    def test_any_card_can_fail_alone(self, admin_client: Client) -> None:
        with patch.object(dashboard, 'top_planets', side_effect=RuntimeError('boom')):
            response = admin_client.get(INDEX)

        assert response.status_code == 200
        assert response.context['top_planets'] is None
        assert response.context['top_shared'] is not None


@pytest.mark.django_db
class TestCaching:
    """AC17: the second request comes from cache, ?refresh=1 recomputes, ?range= changes the windows."""

    def test_second_request_is_cached(self, admin_client: Client, locmem_cache, stats: None) -> None:
        admin_client.get(INDEX)

        with CaptureQueriesContext(connection) as queries:
            assert admin_client.get(INDEX).status_code == 200

        assert len(queries) <= 2, [q['sql'] for q in queries]

    def test_refresh_recomputes(self, admin_client: Client, locmem_cache) -> None:
        with patch.object(dashboard, 'build_dashboard', wraps=dashboard.build_dashboard) as build:
            admin_client.get(INDEX)
            admin_client.get(INDEX)
            admin_client.get(INDEX, {'refresh': '1'})

        assert build.call_count == 2

    @pytest.mark.parametrize('days', [7, 30, 90, 365])
    def test_range_changes_the_windows(self, admin_client: Client, stats: None, days: int) -> None:
        context = admin_client.get(INDEX, {'range': str(days)}).context

        assert context['range_days'] == days
        assert len(chart_labels(context['growth'][0])) == days
        assert all(len(chart_labels(chart)) == days for chart in context['activity'])

    def test_unknown_range_falls_back(self, admin_client: Client) -> None:
        assert admin_client.get(INDEX, {'range': 'nope'}).context['range_days'] == dashboard.DEFAULT_RANGE


@pytest.mark.django_db
class TestHealthChips:
    """AC18: every chip links to a page showing exactly the rows it counts."""

    def test_all_green_collapses_to_one_line(self, admin_client: Client) -> None:
        response = admin_client.get(INDEX)

        assert response.context['all_ok'] is True
        assert 'All checks normal' in response.content.decode()

    def test_stuck_planets_chip(self, admin_client: Client) -> None:
        now = timezone.now()
        stuck = {
            baker.make(GamePlanet, automation_refresh_status='pending', automation_next_retry_at=None).pk,
            baker.make(
                GamePlanet, automation_refresh_status='pending', automation_next_retry_at=now - timedelta(hours=2)
            ).pk,
        }
        baker.make(GamePlanet, automation_refresh_status='pending', automation_next_retry_at=now + timedelta(hours=1))
        baker.make(GamePlanet)

        chip = admin_client.get(INDEX).context['chips'][1]
        linked = admin_client.get(chip['href']).context['cl'].result_list

        assert chip['ok'] is False
        assert 'Planets stuck in pending · 2' in chip['text']
        assert {planet.pk for planet in linked} == stuck

    def test_overdue_tasks_chip(self, admin_client: Client) -> None:
        hourly = baker.make(IntervalSchedule, every=1, period=IntervalSchedule.HOURS)
        now = timezone.now()
        baker.make(PeriodicTask, name='late', task='late', interval=hourly, last_run_at=now - timedelta(hours=5))
        baker.make(PeriodicTask, name='fine', task='fine', interval=hourly, last_run_at=now - timedelta(minutes=5))

        chip = admin_client.get(INDEX).context['chips'][0]
        page = admin_client.get(chip['href'])

        assert chip['ok'] is False
        assert chip['text'].startswith('Task overdue · late')
        assert [row.name for row in page.context['rows']] == ['late']


@pytest.mark.django_db
class TestCards:
    def test_headline_change_and_sparkline(self, admin_client: Client, stats: None) -> None:
        headline = admin_client.get(INDEX, {'range': '7'}).context['headline']
        users = headline['tiles'][0]

        assert users['value'] == '1,000'
        assert users['change']['text'] == '+7 (+0.7%)'
        assert 'spark' in users
        assert 'stickiness 10.0%' in headline['tiles'][1]['sub']

    def test_plans_per_user_buckets(self, admin_client: Client) -> None:
        heavy, light = baker.make('user.User'), baker.make('user.User')
        baker.make('planning.PlanningPlan', user=heavy, plan_permits_used=1, _quantity=7)
        baker.make('planning.PlanningPlan', user=light, plan_permits_used=1)
        baker.make('user.User')  # no plans; plus the superuser

        chart = admin_client.get(INDEX).context['plans_per_user']
        data = json.loads(chart['config']['data'])

        assert data['labels'] == ['0', '1', '2–5', '6–20', '21+']
        assert data['datasets'][0]['data'] == [2, 1, 0, 1, 0]

    def test_top_lists_link_to_rows(self, admin_client: Client) -> None:
        user = baker.make('user.User')
        baker.make('planning.PlanningPlan', user=user, plan_permits_used=1, planet_natural_id='OT-580b', _quantity=3)

        context = admin_client.get(INDEX).context

        assert context['top_planets'][0]['label'] == 'OT-580b'
        assert context['top_planets'][0]['value'] == 3
        assert 'planet_natural_id=OT-580b' in context['top_planets'][0]['href']


@pytest.mark.django_db
class TestEngagementAndAdoption:
    """AC23–AC25: engagement in the range, feature adoption, webhook freshness."""

    def test_engagement_counts_the_range_and_compares_to_the_one_before(self, admin_client: Client) -> None:
        now = timezone.now()
        editor, newcomer, idle = baker.make('user.User'), baker.make('user.User'), baker.make('user.User')
        baker.make('planning.PlanningPlan', user=editor, plan_permits_used=1, _quantity=2)
        baker.make('planning.PlanningPlan', user=newcomer, plan_permits_used=1)
        old = baker.make('planning.PlanningPlan', user=idle, plan_permits_used=1)
        # an edit in the previous 7-day window: counted as "before", not now
        PlanningPlan.objects.filter(pk=old.pk).update(modified_at=now - timedelta(days=10))
        User.objects.filter(pk=idle.pk).update(date_joined=now - timedelta(days=30))
        baker.make('planning.PlanningShared', user=editor, plan=PlanningPlan.objects.filter(user=editor).first())

        tiles = {tile['label']: tile for tile in admin_client.get(INDEX, {'range': '7'}).context['engagement']}

        assert tiles['Active planners, 7 d']['value'] == '2'
        assert tiles['Active planners, 7 d']['change']['text'] == '+1 (+100.0%)'
        assert tiles['Plans edited, 7 d']['value'] == '3'
        # editor, newcomer and the superuser joined this week; two of them have a plan
        assert tiles['Activation, 7 d']['value'] == '66.7%'
        assert tiles['Activation, 7 d']['sub'] == '2 of 3 new users created a plan'
        assert tiles['Shares created, 7 d']['value'] == '1'

    def test_feature_adoption(self, admin_client: Client) -> None:
        planner = baker.make('user.User', prun_username='p', fio_apikey='k')
        baker.make('planning.PlanningPlan', user=planner, plan_permits_used=1, _quantity=3)
        baker.make('user.UserAPIKey', user=planner, name='k', last_used=timezone.now(), revoked=False)
        baker.make('gamedata.GameFIOPlayerData', user=planner, automation_refresh_status='ok')
        baker.make('user.User')  # plus the superuser: 3 users

        rows = {row['label']: row for row in admin_client.get(INDEX).context['feature_adoption']['rows']}

        assert rows['Plans']['text'] == '33.3% · 1'
        assert rows['Empires']['text'] == '0.0% · 0'
        assert rows['API key used in 30 d']['text'] == '33.3% · 1'
        assert rows['FIO credentials']['text'] == '33.3% · 1'
        assert rows['FIO users syncing OK']['text'] == '100.0% · 1'

    def test_webhooks(self, admin_client: Client) -> None:
        baker.make('user.GlobalConfigWebhook', sender='FIO API', total_calls=1234, last_received_at=None)

        (row,) = admin_client.get(INDEX).context['webhooks']

        assert row == ('FIO API', 'active', '1,234', 'never')
