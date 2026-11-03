from planning.models import PlanningCX, PlanningPlan
from rest_framework import serializers


class PlanningCXMinimal(serializers.ModelSerializer):
    class Meta:
        model = PlanningCX
        fields = ['uuid', 'cx_name', 'cx_data']


class PlanningPlanMinimalSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningPlan
        fields = ['uuid', 'plan_name', 'planet_natural_id']
