from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.admin.models import DELETION, LogEntry
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from gamedata.models import GamePlanet
from model_bakery import baker

CHANGELIST = reverse('admin:gamedata_gameplanet_changelist')
DELETE_ALL = f'{CHANGELIST}changelist-delete-all-planets/'


def planet(**fields: object) -> GamePlanet:
    return baker.make(GamePlanet, **fields)  # ty: ignore[no-matching-overload]


@pytest.mark.django_db
class TestDeleteAllPlanets:
    """AC22: GET confirms only; POST needs the current count; one LogEntry."""

    def test_get_shows_the_count_and_deletes_nothing(self, admin_client: Client) -> None:
        planet(), planet(), planet()

        response = admin_client.get(DELETE_ALL)

        assert response.status_code == 200
        assert 'Type <strong>3</strong> to confirm' in response.content.decode()
        assert GamePlanet.objects.count() == 3
        assert LogEntry.objects.count() == 0

    def test_post_with_wrong_count_deletes_nothing(self, admin_client: Client) -> None:
        planet(), planet()

        response = admin_client.post(DELETE_ALL, {'confirm': '3'})

        assert response.status_code == 302
        assert GamePlanet.objects.count() == 2
        assert LogEntry.objects.count() == 0

    def test_post_with_right_count_deletes_all_and_logs_once(self, admin_client: Client) -> None:
        planet(), planet()

        response = admin_client.post(DELETE_ALL, {'confirm': '2'})

        assert response.status_code == 302
        assert GamePlanet.objects.count() == 0
        entry = LogEntry.objects.get()
        assert entry.action_flag == DELETION
        assert entry.change_message == 'Deleted all 2 planets'

    def test_post_without_csrf_is_rejected(self, superuser) -> None:
        planet()
        client = Client(enforce_csrf_checks=True)
        client.force_login(superuser)

        assert client.post(DELETE_ALL, {'confirm': '1'}).status_code == 403
        assert GamePlanet.objects.count() == 1

    def test_no_row_delete_link_and_no_dead_action(self, admin_client: Client) -> None:
        from gamedata.admin.game_planet_admin import GamePlanetAdmin

        assert 'action_delete_planet' not in GamePlanetAdmin.actions_row
        assert not hasattr(GamePlanetAdmin, 'delete_all_planets')


@pytest.mark.django_db
class TestQueuedActions:
    """AC4: actions return at once and enqueue the task (mocked .delay), each with one LogEntry."""

    def test_import_from_fio(self, admin_client: Client) -> None:
        with patch('gamedata.admin.fio_import.gamedata_admin_import.delay') as delay:
            response = admin_client.get(f'{CHANGELIST}changelist-fio-import-all-planet/')

        assert response.status_code == 302
        delay.assert_called_once_with('planets')
        assert LogEntry.objects.count() == 1

    @pytest.mark.parametrize(
        'url_path, task',
        [
            ('changelist-refresh-planet', 'gamedata_refresh_single_planet'),
            ('changelist-refresh-planet-infrastructure', 'gamedata_refresh_planet_infrastructure'),
        ],
    )
    def test_row_refreshes(self, admin_client: Client, url_path: str, task: str) -> None:
        row = planet(planet_natural_id='OT-580b')

        with patch(f'gamedata.admin.game_planet_admin.{task}.delay') as delay:
            response = admin_client.get(f'{CHANGELIST}{row.pk}/{url_path}/')

        assert response.status_code == 302
        delay.assert_called_once_with('OT-580b')
        assert LogEntry.objects.get().object_id == str(row.pk)

    @pytest.mark.parametrize(
        'admin_path, url_path, kind',
        [
            ('gamedata_gamematerial', 'changelist-fio-import-material', 'materials'),
            ('gamedata_gamebuilding', 'changelist-fio-import-building', 'buildings'),
            ('gamedata_gamerecipe', 'changelist-fio-import-recipe', 'recipes'),
            ('gamedata_gameexchange', 'changelist-fio-import-exchange', 'exchanges'),
        ],
    )
    def test_other_imports(self, admin_client: Client, admin_path: str, url_path: str, kind: str) -> None:
        with patch('gamedata.admin.fio_import.gamedata_admin_import.delay') as delay:
            response = admin_client.get(f'{reverse(f"admin:{admin_path}_changelist")}{url_path}/')

        assert response.status_code == 302
        delay.assert_called_once_with(kind)
        assert LogEntry.objects.count() == 1

    def test_aggregator(self, admin_client: Client) -> None:
        url = f'{reverse("admin:analytics_analyticsplanaggregate_changelist")}analytics-aggregate-all/'

        with patch('analytics.admin.analytics_update_plan_insight_aggregates.delay') as delay:
            response = admin_client.get(url)

        assert response.status_code == 302
        delay.assert_called_once_with()
        assert LogEntry.objects.count() == 1

    def test_queue_failure_is_reported_not_raised(self, admin_client: Client) -> None:
        with patch('gamedata.admin.fio_import.gamedata_admin_import.delay', side_effect=ConnectionError('broker')):
            response = admin_client.get(f'{CHANGELIST}changelist-fio-import-all-planet/', follow=True)

        assert response.status_code == 200
        assert 'Could not queue the FIO import of planets.' in response.content.decode()


@pytest.mark.django_db
class TestResetAndRetry:
    """AC8: sets ok, zeroes the error count, enqueues one refresh."""

    def test_selection_action(self, admin_client: Client) -> None:
        failed = planet(planet_natural_id='AA-000a', automation_refresh_status='failed', automation_error_count=10)
        stuck = planet(planet_natural_id='AA-000b', automation_refresh_status='pending', automation_next_retry_at=None)

        with patch('gamedata.admin.game_planet_admin.gamedata_refresh_single_planet.delay') as delay:
            admin_client.post(
                CHANGELIST, {'action': 'action_reset_and_retry', '_selected_action': [failed.pk, stuck.pk]}
            )

        for row in (failed, stuck):
            row.refresh_from_db()
            assert (row.automation_refresh_status, row.automation_error_count) == ('ok', 0)
            assert row.automation_next_retry_at is None
        assert sorted(call.args[0] for call in delay.call_args_list) == ['AA-000a', 'AA-000b']
        assert LogEntry.objects.count() == 1

    def test_detail_action(self, admin_client: Client) -> None:
        row = planet(planet_natural_id='AA-000c', automation_refresh_status='failed', automation_error_count=10)

        with patch('gamedata.admin.game_planet_admin.gamedata_refresh_single_planet.delay') as delay:
            response = admin_client.get(f'{CHANGELIST}{row.pk}/detail-reset-and-retry/')

        assert response.status_code == 302
        row.refresh_from_db()
        assert (row.automation_refresh_status, row.automation_error_count) == ('ok', 0)
        delay.assert_called_once_with('AA-000c')
        assert LogEntry.objects.get().object_id == str(row.pk)


@pytest.mark.django_db
class TestAutomationFilters:
    """AC9: the stuck and permanently-failed filters return exactly their rows."""

    @pytest.fixture
    def rows(self) -> dict[str, GamePlanet]:
        now = timezone.now()
        return {
            'stuck_no_lease': planet(automation_refresh_status='pending', automation_next_retry_at=None),
            'stuck_expired': planet(
                automation_refresh_status='pending', automation_next_retry_at=now - timedelta(minutes=1)
            ),
            'pending_leased': planet(
                automation_refresh_status='pending', automation_next_retry_at=now + timedelta(hours=1)
            ),
            'failed': planet(automation_refresh_status='failed', automation_error_count=10),
            'retrying': planet(automation_refresh_status='retrying', automation_error_count=3),
            'ok': planet(),
        }

    def _listed(self, admin_client: Client, query: dict[str, str]) -> set[str]:
        response = admin_client.get(CHANGELIST, query)
        assert response.status_code == 200
        return {obj.pk for obj in response.context['cl'].result_list}

    def test_stuck(self, admin_client: Client, rows: dict[str, GamePlanet]) -> None:
        listed = self._listed(admin_client, {'stuck': 'lease_expired'})

        assert listed == {rows['stuck_no_lease'].pk, rows['stuck_expired'].pk}

    def test_permanently_failed(self, admin_client: Client, rows: dict[str, GamePlanet]) -> None:
        assert self._listed(admin_client, {'permanently_failed': 'yes'}) == {rows['failed'].pk}


@pytest.mark.django_db
class TestPlanetPages:
    def test_strip_counts(self, admin_client: Client) -> None:
        planet(automation_refresh_status='pending', automation_next_retry_at=None)
        planet(automation_refresh_status='failed', automation_error_count=10)
        planet()

        summary = admin_client.get(CHANGELIST).context['cl'].model_admin.cached_summary(MagicMock())
        tiles = {tile['label']: tile['value'] for tile in summary['tiles']}
        segments = {segment['label']: segment['value'] for segment in summary['segments']}

        assert tiles['Stuck pending'] == '1'
        assert tiles['Permanently failed'] == '1'
        assert tiles['Refreshed in 24 h'] == '3'
        assert segments == {'OK': 1, 'Pending': 1, 'Retrying': 0, 'Failed': 1}

    def test_change_page_header(self, admin_client: Client) -> None:
        row = planet(planet_natural_id='OT-580b', automation_refresh_status='failed', automation_error='FIO said no')
        baker.make('planning.PlanningPlan', planet_natural_id='OT-580b', plan_permits_used=1, _quantity=2)

        html = admin_client.get(reverse('admin:gamedata_gameplanet_change', args=[row.pk])).content.decode()

        assert 'FIO said no' in html
        assert 'Plans on this planet' in html
        assert 'planet_natural_id=OT-580b' in html
        assert 'detail-reset-and-retry' in html
