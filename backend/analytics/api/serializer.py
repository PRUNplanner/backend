from analytics.models import AnalyticsPlanAggregate
from rest_framework import serializers


class AnalyticsPlanAggregateSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()

    class Meta:
        model = AnalyticsPlanAggregate
        fields = ['status', 'planet_natural_id', 'total_plans_analyzed', 'insights_data', 'last_updated']

    def get_status(self, obj) -> str:
        return 'success'
