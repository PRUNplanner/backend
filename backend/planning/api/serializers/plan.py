from api.serializer import PydanticJSONField
from django.db import transaction
from django.shortcuts import get_object_or_404
from planning.models import PlanningEmpire, PlanningEmpirePlan, PlanningPlan, PlanningShared
from planning.schemas.latest_schemas import LATEST_SCHEMA
from rest_framework import serializers

from .cx import PlanningCXDetailSerializer
from .empire import PlanningEmpireListSerializer


class PlanningPlanDetailSerializer(serializers.ModelSerializer):
    empires = PlanningEmpireListSerializer(many=True, read_only=True)
    cx = PlanningCXDetailSerializer(read_only=True)

    # Input-only field for the junction
    empire_uuid = serializers.UUIDField(write_only=True, required=False)

    plan_data = PydanticJSONField(pydantic_model=LATEST_SCHEMA['PLANNING_DATA'])

    class Meta:
        model = PlanningPlan
        fields = [
            'uuid',
            'plan_name',
            'planet_natural_id',
            'plan_permits_used',
            'plan_cogc',
            'plan_corphq',
            'plan_data',
            'empires',
            'cx',
            'empire_uuid',
        ]

    @transaction.atomic
    def create(self, validated_data):
        user = self.context['request'].user
        empire_uuid = validated_data.pop('empire_uuid', None)

        # create plan
        plan = PlanningPlan.objects.create(user=user, **validated_data)

        # handle junction creation
        if empire_uuid:
            self._link_empire(user, plan, empire_uuid)

        return plan

    @transaction.atomic
    def update(self, instance, validated_data):
        user = self.context['request'].user
        empire_uuid = validated_data.pop('empire_uuid', None)

        # update plan fields
        instance = super().update(instance, validated_data)

        # handle junction
        if empire_uuid:
            self._link_empire(user, instance, empire_uuid)

        return instance

    def _link_empire(self, user, plan, empire_uuid):
        # make sure empire exists for this user
        empire = get_object_or_404(PlanningEmpire, uuid=empire_uuid, user=user)

        # use update_or_create: prevents duplicates due to unique_together
        PlanningEmpirePlan.objects.update_or_create(user=user, plan=plan, empire=empire)


class PlanningPlanListSerializer(PlanningPlanDetailSerializer):
    class Meta(PlanningPlanDetailSerializer.Meta):
        fields = [
            'uuid',
            'plan_name',
            'planet_natural_id',
            'plan_permits_used',
            'plan_cogc',
            'plan_corphq',
            'plan_data',
            'empire_uuid',
        ]


class PlanningSharedSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningShared
        fields = [
            'uuid',
            'plan',
            'view_count',
            'created_at',
        ]
        read_only_fields = ['uuid', 'plan', 'view_count', 'created_at']


class PlanningSharedDetailSerializer(serializers.ModelSerializer):
    plan_details = PlanningPlanDetailSerializer(source='plan', read_only=True)

    class Meta:
        model = PlanningShared
        fields = [
            'uuid',
            'plan_details',
            'view_count',
            'created_at',
        ]
        read_only_fields = fields


class PlanningSharedCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningShared
        fields = ['plan']

    def validate_plan(self, value):
        if value.user != self.context['request'].user:
            raise serializers.ValidationError('You do not have permission to share this plan.')
        return value
