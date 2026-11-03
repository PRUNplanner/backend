from planning.models import PlanningCX, PlanningPlan
from rest_framework import serializers


class PlanningCXMinimal(serializers.ModelSerializer):
    class Meta:
        model = PlanningCX
        fields = ['uuid', 'cx_name', 'cx_data']


# what PlanningPlanMinimalSerializer reads: prefetches for it load only these, never plan_data
MINIMAL_PLAN_FIELDS = ('uuid', 'plan_name', 'planet_natural_id')


class PlanningPlanMinimalSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningPlan
        fields = list(MINIMAL_PLAN_FIELDS)
