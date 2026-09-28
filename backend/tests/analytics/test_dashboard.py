import json
from collections.abc import Callable
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
from planning.models import PlanningCX, PlanningEmpire, PlanningEmpirePlan, PlanningPlan
from planning.signup_defaults import SIGNUP_CX_DATA, SIGNUP_EMPIRE
from user.api.serializer import UserRegisterSerializer
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
        assert rows['Empire set up']['text'] == '0.0% · 0'
        assert 'Empires' not in rows and 'CX preferences' not in rows
        assert rows['API key used in 30 d']['text'] == '33.3% · 1'
        assert rows['FIO credentials']['text'] == '33.3% · 1'
        assert rows['FIO users syncing OK']['text'] == '100.0% · 1'

    def test_webhooks(self, admin_client: Client) -> None:
        baker.make('user.GlobalConfigWebhook', sender='FIO API', total_calls=1234, last_received_at=None)

        (row,) = admin_client.get(INDEX).context['webhooks']

        assert row == ('FIO API', 'active', '1,234', 'never')


def seeded_empire(user: User) -> PlanningEmpire:
    """Another empire identical to the one signup seeds."""
    return baker.make(
        PlanningEmpire,
        user=user,
        empire_name='My Empire',
        empire_faction='NONE',
        empire_permits_used=1,
        empire_permits_total=2,
    )


def signup(username: str) -> User:
    """A user created the way registration creates one: seeded CX preference and empire, no plans."""
    serializer = UserRegisterSerializer(
        data={'username': username, 'password': 'a-long-pass-123', 'planet_id': 'UV-351c', 'planet_input': 'umbra'}
    )
    serializer.is_valid(raise_exception=True)
    return serializer.save()


def adoption_rows(client: Client) -> dict[str, str]:
    return {row['label']: row['text'] for row in client.get(INDEX).context['feature_adoption']['rows']}


@pytest.mark.django_db
class TestDeliberateUse:
    """Seeded signup rows don't count as using empires or CX preferences; changing or adding to them does."""

    def test_signup_creates_exactly_the_defaults(self) -> None:
        user = signup('fresh')

        (empire,) = PlanningEmpire.objects.filter(user=user)
        (cx,) = PlanningCX.objects.filter(user=user)
        assert {field: getattr(empire, field) for field in SIGNUP_EMPIRE} == SIGNUP_EMPIRE
        assert empire.cx == cx
        assert cx.cx_data == SIGNUP_CX_DATA

    def test_a_fresh_signup_counts_as_nothing(self) -> None:
        user = signup('fresh')

        assert not dashboard.empire_set_up().filter(pk=user.pk).exists()
        assert not dashboard.empire_in_use().filter(pk=user.pk).exists()
        assert not dashboard.pricing_customised().filter(pk=user.pk).exists()

    @pytest.mark.parametrize(
        'change',
        [
            {'empire_name': 'Test Empire'},
            {'empire_faction': 'MORIA'},
            {'empire_permits_used': 2},
            {'empire_permits_total': 3},
        ],
    )
    def test_changing_the_empire_sets_it_up(self, change: dict[str, str | int]) -> None:
        user = signup('fresh')
        PlanningEmpire.objects.filter(user=user).update(**change)

        assert list(dashboard.empire_set_up()) == [user]
        assert not dashboard.pricing_customised().exists()

    def test_a_second_empire_sets_it_up(self) -> None:
        user = signup('fresh')
        seeded_empire(user)

        assert list(dashboard.empire_set_up()) == [user]

    def test_editing_or_adding_a_cx_customises_pricing(self) -> None:
        edited, added = signup('edited'), signup('added')
        PlanningCX.objects.filter(user=edited).update(cx_data={**SIGNUP_CX_DATA, 'cx_empire': []})
        baker.make(PlanningCX, user=added, cx_data=SIGNUP_CX_DATA)

        assert set(dashboard.pricing_customised()) == {edited, added}
        assert not dashboard.empire_set_up().exists()

    def test_an_empire_with_two_plans_is_in_use(self) -> None:
        busy, single = signup('busy'), signup('single')
        for user, plans in ((busy, 2), (single, 1)):
            empire = PlanningEmpire.objects.get(user=user)
            for plan in baker.make(PlanningPlan, user=user, plan_permits_used=1, _quantity=plans):
                baker.make(PlanningEmpirePlan, user=user, empire=empire, plan=plan)

        assert list(dashboard.empire_in_use()) == [busy]

    def test_feature_adoption_rows(self, admin_client: Client) -> None:
        signup('fresh')
        PlanningEmpire.objects.filter(user=signup('configured')).update(empire_name='Mine')

        rows = adoption_rows(admin_client)

        # 3 users with the superuser
        assert rows['Empire set up'] == '33.3% · 1'
        assert rows['Empire with 2+ plans'] == '0.0% · 0'
        assert rows['Pricing customised'] == '0.0% · 0'

    def test_headline_counts_deliberate_use(self, admin_client: Client) -> None:
        PlanningCX.objects.filter(user=signup('pricer')).update(cx_data={})
        signup('fresh')

        tiles = {tile['label']: tile for tile in admin_client.get(INDEX).context['headline']['tiles']}

        assert tiles['Empires set up']['value'] == '0'
        assert tiles['Pricing customised']['value'] == '1'
        assert 'Empires' not in tiles and 'CX preferences' not in tiles

    def test_activity_skips_rows_seeded_by_signup(self, admin_client: Client) -> None:
        user = signup('fresh')
        later = seeded_empire(user)
        PlanningEmpire.objects.filter(pk=later.pk).update(created_at=user.date_joined + timedelta(hours=1))

        charts = admin_client.get(INDEX, {'range': '7'}).context['activity']
        titles = [chart['title'] for chart in charts]

        assert titles[2] == 'Empires created by users (1 in 7 d)'
        assert titles[3] == 'CX preferences created by users (0 in 7 d)'


@pytest.mark.django_db
class TestOnboardingFunnel:
    def test_steps_and_median(self, admin_client: Client) -> None:
        now = timezone.now()
        # joined before the window: not counted
        User.objects.filter(pk=signup('old').pk).update(date_joined=now - timedelta(days=30))
        signup('idle')
        PlanningEmpire.objects.filter(user=signup('configured')).update(empire_faction='HORTUS')
        for name, wait in (('fast', timedelta(minutes=10)), ('slow', timedelta(hours=3))):
            user = signup(name)
            plan = baker.make(PlanningPlan, user=user, plan_permits_used=1)
            PlanningPlan.objects.filter(pk=plan.pk).update(created_at=user.date_joined + wait)
            PlanningCX.objects.filter(user=user).update(cx_data={})

        funnel = admin_client.get(INDEX, {'range': '7'}).context['onboarding']
        rows = {row['label']: row for row in funnel['rows']}

        # idle, configured, fast, slow and the superuser
        assert rows['Signed up']['text'] == '100.0% · 5'
        assert 'date_joined_from_0=' in rows['Signed up']['href']
        assert rows['Set up their empire']['text'] == '20.0% · 1'
        assert rows['Created a plan']['text'] == '40.0% · 2'
        assert rows['Customised pricing']['text'] == '40.0% · 2'
        # median of 10 min and 3 h
        assert funnel['median_to_first_plan'] == '1.6 h'

    def test_no_signups(self) -> None:
        User.objects.all().delete()

        funnel = dashboard.onboarding_funnel(7)

        assert funnel['median_to_first_plan'] == '—'
        assert all(row['text'] == '0.0% · 0' for row in funnel['rows'])


@pytest.mark.django_db
class TestQueryCounts:
    """Each metric is a fixed number of queries, whatever the number of users."""

    @pytest.mark.parametrize(
        ('card', 'queries'),
        [
            (dashboard.feature_adoption, 9),
            (lambda: dashboard.onboarding_funnel(30), 5),
            (lambda: dashboard.headline(30), 4),
        ],
    )
    @pytest.mark.parametrize('users', [1, 5])
    def test_fixed_query_count(
        self, django_assert_num_queries: Callable, card: Callable[[], object], queries: int, users: int
    ) -> None:
        for n in range(users):
            user = signup(f'user{n}')
            PlanningEmpire.objects.filter(user=user).update(empire_name=f'Empire {n}')
            baker.make(PlanningPlan, user=user, plan_permits_used=1)

        with django_assert_num_queries(queries):
            card()
