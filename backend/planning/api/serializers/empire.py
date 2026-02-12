from django.db import transaction
from planning.models import PlanningEmpire
from rest_framework import serializers

from .minimal import PlanningCXMinimal, PlanningPlanMinimalSerializer


class PlanningEmpireNestedSerializer(serializers.ModelSerializer):
    plans = PlanningPlanMinimalSerializer(many=True, read_only=True)

    class Meta:
        model = PlanningEmpire
        fields = ['uuid', 'empire_name', 'plans']


class PlanningEmpireListSerializer(serializers.ModelSerializer):
    cx = PlanningCXMinimal(read_only=True)

    class Meta:
        model = PlanningEmpire
        fields = ['uuid', 'empire_name', 'empire_faction', 'empire_permits_used', 'empire_permits_total', 'cx']


class PlanningEmpireDetailSerializer(serializers.ModelSerializer):
    plans = PlanningPlanMinimalSerializer(many=True, read_only=True)
    cx = PlanningCXMinimal(read_only=True)

    class Meta:
        model = PlanningEmpire
        exclude = ['created_at', 'modified_at', 'user']

    @transaction.atomic
    def update(self, instance, validated_data):

        instance = super().update(instance, validated_data)

        return instance

    @transaction.atomic
    def create(self, validated_data):
        user = self.context['request'].user

        empire = PlanningEmpire.objects.create(user=user, **validated_data)

        return empire


class PlanningEmpireJunctionsPlanSerializer(serializers.Serializer):
    baseplanner_uuid = serializers.UUIDField()


class PlanningEmpireJunctionsSerializer(serializers.Serializer):
    empire_uuid = serializers.UUIDField()
    baseplanners = PlanningEmpireJunctionsPlanSerializer(many=True)


class PlanningEmpirePlanSyncErrorSerializer(serializers.Serializer):
    error = serializers.CharField(default='Unauthorized or non-existent objects referenced')
    invalid_empires = serializers.ListField(child=serializers.UUIDField())
    invalid_plans = serializers.ListField(child=serializers.UUIDField())


class PlanningEmpirePlanSyncSuccessSerializer(serializers.Serializer):
    status = serializers.CharField(default='Empire-Plan junctions updated')
