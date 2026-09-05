from collections.abc import Callable

import pytest
from analytics.models import AnalyticsPlanAggregate
from django.urls import reverse
from gamedata.models.game_planet import GamePlanet
from rest_framework.test import APIClient
from user.models import User

pytestmark = pytest.mark.django_db


def _detail_url(planet_natural_id: str) -> str:
    return reverse('analytics:planet-insight-detail', kwargs={'planet_natural_id': planet_natural_id})


class TestAnalyticsPlanAggregateViewSetAccess:
    def test_unauthenticated_access_is_allowed(
        self, api_client: APIClient, planet_factory: Callable[..., GamePlanet]
    ) -> None:
        planet = planet_factory(planet_natural_id='OT-580b')

        response = api_client.get(_detail_url(planet.planet_natural_id))

        assert response.status_code == 200

    def test_authenticated_access_also_succeeds(
        self,
        api_client: APIClient,
        planet_factory: Callable[..., GamePlanet],
        user_factory: Callable[..., User],
    ) -> None:
        planet = planet_factory(planet_natural_id='OT-580b')
        user = user_factory()

        response = api_client.as_user(user).get(_detail_url(planet.planet_natural_id))  # ty:ignore[unresolved-attribute]

        assert response.status_code == 200

    def test_disallowed_http_method_returns_405(
        self, api_client: APIClient, planet_factory: Callable[..., GamePlanet]
    ) -> None:
        planet = planet_factory(planet_natural_id='OT-580b')

        response = api_client.post(_detail_url(planet.planet_natural_id), data={})

        assert response.status_code == 405


class TestAnalyticsPlanAggregateViewSetRetrieve:
    def test_retrieve_returns_serialized_aggregate(
        self,
        api_client: APIClient,
        planet_factory: Callable[..., GamePlanet],
        plan_aggregate_factory: Callable[..., AnalyticsPlanAggregate],
    ) -> None:
        planet = planet_factory(planet_natural_id='OT-580b')
        aggregate = plan_aggregate_factory(
            planet_natural_id=planet.planet_natural_id,
            total_plans_analyzed=42,
            insights_data={'avg_cost': 1234.5, 'materials': ['H2O', 'DW']},
        )

        response = api_client.get(_detail_url(planet.planet_natural_id))

        assert response.status_code == 200
        assert response.data['status'] == 'success'
        assert response.data['planet_natural_id'] == planet.planet_natural_id
        assert response.data['total_plans_analyzed'] == 42
        assert response.data['insights_data'] == {'avg_cost': 1234.5, 'materials': ['H2O', 'DW']}
        assert response.data['last_updated'] is not None
        assert response['X-Cache-Hit'] == '0'
        assert aggregate.pk is not None

    def test_retrieve_planet_without_aggregate_returns_below_threshold(
        self, api_client: APIClient, planet_factory: Callable[..., GamePlanet]
    ) -> None:
        planet = planet_factory(planet_natural_id='OT-580b')

        response = api_client.get(_detail_url(planet.planet_natural_id))

        assert response.status_code == 200
        assert response.data == {
            'status': 'below_threshold',
            'planet_natural_id': planet.planet_natural_id,
            'total_plans_analyzed': 0,
            'aggregated_data': None,
        }

    def test_retrieve_unknown_planet_returns_404(self, api_client: APIClient) -> None:
        response = api_client.get(_detail_url('XX-000x'))

        assert response.status_code == 404
        assert response.data['detail'] == 'Planet not found.'

    def test_retrieve_only_returns_data_for_requested_planet(
        self,
        api_client: APIClient,
        planet_factory: Callable[..., GamePlanet],
        plan_aggregate_factory: Callable[..., AnalyticsPlanAggregate],
    ) -> None:
        planet_a = planet_factory(planet_natural_id='OT-580b')
        planet_b = planet_factory(planet_natural_id='ZV-759b')

        plan_aggregate_factory(planet_natural_id=planet_a.planet_natural_id, total_plans_analyzed=5)
        plan_aggregate_factory(planet_natural_id=planet_b.planet_natural_id, total_plans_analyzed=99)

        response_a = api_client.get(_detail_url(planet_a.planet_natural_id))
        response_b = api_client.get(_detail_url(planet_b.planet_natural_id))

        assert response_a.data['total_plans_analyzed'] == 5
        assert response_b.data['total_plans_analyzed'] == 99

    def test_retrieve_empty_database_does_not_error(self, api_client: APIClient) -> None:
        assert AnalyticsPlanAggregate.objects.count() == 0
        assert GamePlanet.objects.count() == 0

        response = api_client.get(_detail_url('OT-580b'))

        assert response.status_code == 404
