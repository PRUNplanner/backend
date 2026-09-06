from analytics.models import AnalyticsPlanAggregate
from rest_framework import serializers


class AnalyticsPlanAggregateSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()

    class Meta:
        model = AnalyticsPlanAggregate
        fields = ['status', 'planet_natural_id', 'total_plans_analyzed', 'insights_data', 'last_updated']

    def get_status(self, obj) -> str:
        return 'success'


class AnalyticsMarketInsightSerializer(serializers.Serializer):
    data = serializers.ListField(
        child=serializers.ListField(
            child=serializers.CharField(),
            min_length=1,
            max_length=3,
            help_text='Indices: 0: ticker, 1: production, 2: consumption, 3: delta',
        )
    )
