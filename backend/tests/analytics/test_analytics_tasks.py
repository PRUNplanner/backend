from unittest.mock import patch

import pytest
from analytics.models import AnalyticsEmpireMaterialSnapshot
from analytics.tasks import analytics_bulk_materialize_empire_snapshots
from django.urls import reverse
from model_bakery import baker
from planning.models import PlanningEmpire
from planning.services.empire_state_service import EmpireStateService

pytestmark = pytest.mark.django_db


def _state(p: float) -> dict[str, object]:
    return {'empire_total': {'H2O': {'p': p, 'c': 1.0, 'd': p - 1.0}}}


class TestBulkMaterializeEmpireSnapshots:
    def test_materializes_dirty_empires_and_clears_flag(self) -> None:
        empire: PlanningEmpire = baker.make('planning.PlanningEmpire', empire_state=_state(5.0), needs_state_sync=True)

        analytics_bulk_materialize_empire_snapshots()

        empire.refresh_from_db()
        assert empire.needs_state_sync is False
        snapshot = AnalyticsEmpireMaterialSnapshot.objects.get(empire=empire, material_ticker='H2O')
        assert float(snapshot.production) == 5.0

    @pytest.mark.usefixtures('locmem_cache')
    def test_leaves_planning_caches_alone(self, api_client, user_factory, django_capture_on_commit_callbacks) -> None:
        user = user_factory()
        baker.make('planning.PlanningEmpire', user=user, empire_state=_state(5.0), needs_state_sync=True)
        url = reverse('planning:empire')
        api_client.as_user(user).get(url)

        with django_capture_on_commit_callbacks(execute=True):
            analytics_bulk_materialize_empire_snapshots()

        assert api_client.as_user(user).get(url)['X-Cache-Hit'] == '1'

    @pytest.mark.usefixtures('locmem_cache')
    def test_refreshes_the_global_tracker_only_when_something_changed(self, api_client) -> None:
        url = reverse('analytics:planning-insight-materials')
        api_client.get(url)

        analytics_bulk_materialize_empire_snapshots()
        assert api_client.get(url)['X-Cache-Hit'] == '1'

        baker.make('planning.PlanningEmpire', empire_state=_state(5.0), needs_state_sync=True)
        analytics_bulk_materialize_empire_snapshots()
        response = api_client.get(url)
        assert response['X-Cache-Hit'] == '0'
        assert response.data[0][0] == 'H2O'

    def test_state_synced_during_run_stays_dirty(self) -> None:
        empire: PlanningEmpire = baker.make('planning.PlanningEmpire', empire_state=_state(5.0), needs_state_sync=True)
        real_sync_snapshot = EmpireStateService.sync_snapshot

        def snapshot_then_user_syncs(target: PlanningEmpire) -> None:
            real_sync_snapshot(target)
            # the user pushes a newer state while the task is still running
            EmpireStateService.update_state(PlanningEmpire.objects.get(pk=target.pk), _state(9.0))

        with patch('analytics.tasks.EmpireStateService.sync_snapshot', side_effect=snapshot_then_user_syncs):
            analytics_bulk_materialize_empire_snapshots()

        empire.refresh_from_db()
        assert empire.empire_state == _state(9.0)
        assert empire.needs_state_sync is True
