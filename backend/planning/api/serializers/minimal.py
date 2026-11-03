from planning.models import PlanningCX, PlanningEmpire, PlanningPlan
from rest_framework import serializers


class PlanningEmpireMinimalSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningEmpire
        fields = ['uuid', 'empire_name', 'empire_faction', 'empire_permits_used', 'empire_permits_total']


class PlanningEmpireSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningEmpire
        exclude = ['created_at', 'modified_at', 'user']


class PlanningCXMinimal(serializers.ModelSerializer):
    class Meta:
        model = PlanningCX
        exclude = ['created_at', 'modified_at', 'schema_version', 'user']


# what PlanningPlanMinimalSerializer reads: prefetches for it load only these, never plan_data
MINIMAL_PLAN_FIELDS = ('uuid', 'plan_name', 'planet_natural_id')


class PlanningPlanMinimalSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningPlan
        fields = list(MINIMAL_PLAN_FIELDS)
