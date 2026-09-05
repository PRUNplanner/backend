from collections.abc import Callable
from datetime import timedelta

import pytest
from analytics.models import AnalyticsEmpireMaterialSnapshot
from django.urls import reverse
from django.utils import timezone
from planning.models import PlanningEmpire
from rest_framework.test import APIClient
from user.models import User

pytestmark = pytest.mark.django_db


def _url() -> str:
    return reverse('analytics:planning-insight-materials')


def _rows_by_ticker(data: list[list[object]]) -> dict[object, list[object]]:
    return {row[0]: row for row in data}


class TestAnalyticsMarketInsightViewSetAccess:
    def test_unauthenticated_access_is_allowed(self, api_client: APIClient) -> None:
        response = api_client.get(_url())

        assert response.status_code == 200

    def test_authenticated_access_also_succeeds(self, api_client: APIClient, user_factory: Callable[..., User]) -> None:
        user = user_factory()

        response = api_client.as_user(user).get(_url())  # ty:ignore[unresolved-attribute]

        assert response.status_code == 200

    def test_disallowed_http_method_returns_405(self, api_client: APIClient) -> None:
        response = api_client.post(_url(), data={})

        assert response.status_code == 405


class TestAnalyticsMarketInsightViewSetGlobalMaterials:
    def test_empty_database_returns_empty_list(self, api_client: APIClient) -> None:
        assert AnalyticsEmpireMaterialSnapshot.objects.count() == 0

        response = api_client.get(_url())

        assert response.status_code == 200
        assert response.data == []

    def test_aggregates_production_consumption_and_delta_per_material(
        self,
        api_client: APIClient,
        empire_factory: Callable[..., PlanningEmpire],
        material_snapshot_factory: Callable[..., AnalyticsEmpireMaterialSnapshot],
    ) -> None:
        empire_a = empire_factory()
        empire_b = empire_factory()

        material_snapshot_factory(empire=empire_a, material_ticker='H2O', production=100, consumption=40, delta=60)
        material_snapshot_factory(empire=empire_b, material_ticker='H2O', production=50, consumption=10, delta=40)
        material_snapshot_factory(empire=empire_a, material_ticker='DW', production=5, consumption=5, delta=0)

        response = api_client.get(_url())

        assert response.status_code == 200
        rows = _rows_by_ticker(response.data)

        assert rows['H2O'] == ['H2O', 150.0, 50.0, 100.0]
        assert rows['DW'] == ['DW', 5.0, 5.0, 0.0]

    def test_results_are_ordered_by_material_ticker(
        self,
        api_client: APIClient,
        empire_factory: Callable[..., PlanningEmpire],
        material_snapshot_factory: Callable[..., AnalyticsEmpireMaterialSnapshot],
    ) -> None:
        empire = empire_factory()

        material_snapshot_factory(empire=empire, material_ticker='RAT')
        material_snapshot_factory(empire=empire, material_ticker='DW')
        material_snapshot_factory(empire=empire, material_ticker='H2O')

        response = api_client.get(_url())

        tickers = [row[0] for row in response.data]
        assert tickers == ['DW', 'H2O', 'RAT']

    def test_excludes_snapshots_from_empires_inactive_for_over_30_days(
        self,
        api_client: APIClient,
        empire_factory: Callable[..., PlanningEmpire],
        material_snapshot_factory: Callable[..., AnalyticsEmpireMaterialSnapshot],
    ) -> None:
        active_empire = empire_factory()
        stale_empire = empire_factory()

        material_snapshot_factory(empire=active_empire, material_ticker='H2O', production=10, consumption=0, delta=10)
        material_snapshot_factory(empire=stale_empire, material_ticker='H2O', production=999, consumption=0, delta=999)

        stale_cutoff = timezone.now() - timedelta(days=40)
        PlanningEmpire.objects.filter(pk=stale_empire.pk).update(modified_at=stale_cutoff)

        response = api_client.get(_url())

        assert response.status_code == 200
        rows = _rows_by_ticker(response.data)
        assert rows['H2O'] == ['H2O', 10.0, 0.0, 10.0]
