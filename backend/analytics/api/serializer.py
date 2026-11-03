from analytics.models import AnalyticsPlanAggregate
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers


class AnalyticsPlanAggregateSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()

    class Meta:
        model = AnalyticsPlanAggregate
        fields = ['status', 'planet_natural_id', 'total_plans_analyzed', 'total_users', 'insights_data', 'last_updated']

    @extend_schema_field(serializers.ChoiceField(choices=['success']))
    def get_status(self, obj) -> str:
        return 'success'


class AnalyticsPlanBelowThresholdSerializer(serializers.Serializer):
    """Docs only: the planet insights answer for a planet without enough plans."""

    status = serializers.ChoiceField(choices=['below_threshold'])
    planet_natural_id = serializers.CharField()
    total_plans_analyzed = serializers.IntegerField()
    insights_data = serializers.JSONField(allow_null=True)


# [ticker, production, consumption, delta]; OpenAPI 3.0 has no tuples
MATERIAL_INSIGHTS_SCHEMA = {
    'type': 'array',
    'items': {
        'type': 'array',
        'items': {'oneOf': [{'type': 'string'}, {'type': 'number'}]},
        'minItems': 4,
        'maxItems': 4,
        'description': '[ticker, production, consumption, delta]: a string and 3 numbers',
    },
}
