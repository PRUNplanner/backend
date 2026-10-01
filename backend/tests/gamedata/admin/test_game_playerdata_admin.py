from unittest.mock import patch

import pytest
from django.contrib.admin.models import LogEntry
from django.test import Client
from django.urls import reverse
from gamedata.models import GameFIOPlayerData
from model_bakery import baker

CHANGELIST = reverse('admin:gamedata_gamefioplayerdata_changelist')


@pytest.mark.django_db
class TestFIOPlayerDataAdmin:
    def test_refresh_is_queued_not_run(self, admin_client: Client) -> None:
        """A4: the refresh action enqueues per user with credentials, never fetches in the request."""
        linked = baker.make(GameFIOPlayerData, user=baker.make('user.User', prun_username='a', fio_apikey='k'))
        unlinked = baker.make(GameFIOPlayerData, user=baker.make('user.User'))

        with (
            patch('gamedata.admin.game_playerdata_admin.gamedata_refresh_user_fiodata.delay') as delay,
            patch('gamedata.tasks.gamedata_refresh_user_fiodata.run') as run,
        ):
            admin_client.post(
                CHANGELIST, {'action': 'action_user_refresh_fio', '_selected_action': [linked.pk, unlinked.pk]}
            )

        delay.assert_called_once_with(linked.user.pk)
        run.assert_not_called()
        assert LogEntry.objects.count() == 1

    def test_reset_and_retry_drops_the_cooldown_lock(self, admin_client: Client) -> None:
        data = baker.make(GameFIOPlayerData, automation_refresh_status='failed', automation_error_count=10)

        with (
            patch('gamedata.admin.game_playerdata_admin.gamedata_refresh_user_fiodata.delay') as delay,
            patch('gamedata.admin.game_playerdata_admin.GamedataCacheManager.delete_fio_refresh_lock') as unlock,
        ):
            admin_client.post(CHANGELIST, {'action': 'action_reset_and_retry', '_selected_action': [data.pk]})

        data.refresh_from_db()
        assert (data.automation_refresh_status, data.automation_error_count) == ('ok', 0)
        unlock.assert_called_once_with(data.user.pk)
        delay.assert_called_once_with(data.user.pk)

    def test_changelist_defers_the_payloads(self, admin_client: Client) -> None:
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        baker.make(GameFIOPlayerData)

        with CaptureQueriesContext(connection) as queries:
            assert admin_client.get(CHANGELIST).status_code == 200

        selects = [q['sql'] for q in queries if 'prunplanner_game_fio_playerdata' in q['sql']]
        assert selects
        assert not any('"storage_data"' in sql for sql in selects)

    def test_list_and_detail_show_what_fio_answered(self, admin_client: Client) -> None:
        rejected = baker.make(GameFIOPlayerData, fio_status_code=401, automation_error_count=10)
        baker.make(GameFIOPlayerData, fio_status_code=200)

        listed = admin_client.get(CHANGELIST, {'fio_status_code__exact': 401}).context['cl'].result_list
        detail = admin_client.get(reverse('admin:gamedata_gamefioplayerdata_change', args=[rejected.pk]))

        assert [row.pk for row in listed] == [rejected.pk]
        assert 'Key rejected' in detail.content.decode()
