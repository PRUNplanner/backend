from collections import Counter

import pytest
from analytics.services.planinsight_aggregator_service import PlanInsightAggregatorService

pytestmark = pytest.mark.django_db


class TestRecipeDistribution:
    def test_keys_recipes_by_their_building(self) -> None:
        service = PlanInsightAggregatorService()

        result = service._get_recipe_distribution({'BMP': Counter({'BMP#A=>B': 8, 'BMP#C=>D': 2})}, ['BMP'])

        assert result == {
            'BMP': [{'recipe_id': 'BMP#A=>B', 'percentage': 80.0}, {'recipe_id': 'BMP#C=>D', 'percentage': 20.0}]
        }

    def test_result_key_is_the_building_not_the_recipe_prefix(self) -> None:
        service = PlanInsightAggregatorService()

        result = service._get_recipe_distribution({'BMP': Counter({'PP1#A=>B': 10})}, ['BMP', 'PP1'])

        assert list(result) == ['BMP']
