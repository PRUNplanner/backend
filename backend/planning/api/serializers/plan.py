from typing import Any

from api.serializer import PydanticJSONField
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from gamedata.models import GamePlanetCOGCProgram
from gamedata.models.game_planet import GamePlanetCOGCProgramChoices
from planning.models import (
    PlanningCOGCChoices,
    PlanningEmpire,
    PlanningEmpirePlan,
    PlanningPlan,
    PlanningPlanVersion,
    PlanningShared,
)
from planning.schemas.latest_schemas import LATEST_SCHEMA
from planning.services import plan_version_service
from rest_framework import serializers

from .conflict import SaveConflictMixin
from .cx import PlanningCXDetailSerializer
from .empire import PlanningEmpireListSerializer

COGC_MAP: dict[GamePlanetCOGCProgramChoices, PlanningCOGCChoices] = {
    GamePlanetCOGCProgramChoices.Agriculture: PlanningCOGCChoices.AGRICULTURE,
    GamePlanetCOGCProgramChoices.Chemistry: PlanningCOGCChoices.CHEMISTRY,
    GamePlanetCOGCProgramChoices.Construction: PlanningCOGCChoices.CONSTRUCTION,
    GamePlanetCOGCProgramChoices.Electronics: PlanningCOGCChoices.ELECTRONICS,
    GamePlanetCOGCProgramChoices.Food_Industries: PlanningCOGCChoices.FOOD_INDUSTRIES,
    GamePlanetCOGCProgramChoices.Fuel_Refining: PlanningCOGCChoices.FUEL_REFINING,
    GamePlanetCOGCProgramChoices.Manufacturing: PlanningCOGCChoices.MANUFACTURING,
    GamePlanetCOGCProgramChoices.Metallurgy: PlanningCOGCChoices.METALLURGY,
    GamePlanetCOGCProgramChoices.Resource_Extraction: PlanningCOGCChoices.RESOURCE_EXTRACTION,
    GamePlanetCOGCProgramChoices.Workforce_Pioneers: PlanningCOGCChoices.PIONEERS,
    GamePlanetCOGCProgramChoices.Workforce_Settlers: PlanningCOGCChoices.SETTLERS,
    GamePlanetCOGCProgramChoices.Workforce_Technicians: PlanningCOGCChoices.TECHNICIANS,
    GamePlanetCOGCProgramChoices.Workforce_Engineers: PlanningCOGCChoices.ENGINEERS,
    GamePlanetCOGCProgramChoices.Workforce_Scientists: PlanningCOGCChoices.SCIENTISTS,
}


class PlanningPlanDetailSerializer(SaveConflictMixin, serializers.ModelSerializer):
    empires = PlanningEmpireListSerializer(many=True, read_only=True)
    cx = PlanningCXDetailSerializer(read_only=True)

    # Input-only field for the junction
    empire_uuid = serializers.UUIDField(write_only=True, required=False)

    # Input-only fields for the version this save creates
    version_name = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=80)
    restored_from = serializers.UUIDField(write_only=True, required=False, allow_null=True)

    plan_data = PydanticJSONField(pydantic_model=LATEST_SCHEMA['PLANNING_DATA'])

    # COGC: must allow null, if so: populate from planets running cogc
    plan_cogc = serializers.ChoiceField(choices=PlanningCOGCChoices.choices, allow_null=True, required=False)

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
            'version_name',
            'restored_from',
            'modified_at',
            'base_modified_at',
        ]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:

        # plan_cogc
        if attrs.get('plan_cogc') is None:
            # extract planet_natural_id from attrs (CREATE/PUT) or instance (PATCH) if missing
            planet_id = attrs.get('planet_natural_id')
            if planet_id is None and self.instance:
                planet_id = self.instance.planet_natural_id

            if planet_id:
                attrs['plan_cogc'] = self._calculate_planet_cogc(planet_id)
            else:
                attrs['plan_cogc'] = PlanningCOGCChoices.NONE

        return attrs

    def _calculate_planet_cogc(self, planet_natural_id: str) -> str:
        # look up the active cogc program for the planet or default to NONE

        now_ms = int(timezone.now().timestamp() * 1000)

        raw_active_program_type = (
            GamePlanetCOGCProgram.objects.filter(
                planet__planet_natural_id=planet_natural_id, start_epochms__lte=now_ms, end_epochms__gte=now_ms
            )
            .values_list('program_type', flat=True)
            .first()
        )

        if raw_active_program_type:
            mapped_value = COGC_MAP.get(raw_active_program_type)

            # return found type if existing and valid
            if mapped_value:
                return mapped_value

        # else fallback to NONE
        return PlanningCOGCChoices.NONE

    @transaction.atomic
    def create(self, validated_data):
        user = self.context['request'].user
        empire_uuid = validated_data.pop('empire_uuid', None)
        validated_data.pop('version_name', None)
        validated_data.pop('restored_from', None)

        # create plan
        plan = PlanningPlan.objects.create(user=user, **validated_data)
        plan_version_service.record_created(plan)

        # handle junction creation
        if empire_uuid:
            self._link_empire(user, plan, empire_uuid)

        return plan

    @transaction.atomic
    def update(self, instance, validated_data):
        _user = self.context['request'].user
        _empire_uuid = validated_data.pop('empire_uuid', None)
        version_name = validated_data.pop('version_name', '')
        restored_from = validated_data.pop('restored_from', None)

        # also without base_modified_at: saves must reach the versions in the order they reach the row
        before = get_object_or_404(PlanningPlan.objects.select_for_update(), pk=instance.pk)

        # under the lock: another save may prune the version
        source = PlanningPlanVersion.objects.filter(pk=restored_from, plan=instance).first() if restored_from else None
        if restored_from and source is None:
            raise serializers.ValidationError({'restored_from': 'Not a version of this plan.'})

        # update plan fields
        instance = super().update(instance, validated_data)
        plan_version_service.record_save(instance, before=before, name=version_name, restored_from=source)

        # no junction update on update, only on create

        # handle junction
        # if empire_uuid:
        #     self._link_empire(user, instance, empire_uuid)

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
            'modified_at',
        ]


class PlanningSharedPlanSerializer(PlanningPlanDetailSerializer):
    # public payload of a shared plan: never the owner's empires or CX
    class Meta(PlanningPlanDetailSerializer.Meta):
        fields = [
            'uuid',
            'plan_name',
            'planet_natural_id',
            'plan_permits_used',
            'plan_cogc',
            'plan_corphq',
            'plan_data',
        ]
        read_only_fields = fields


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
    plan_details = PlanningSharedPlanSerializer(source='plan', read_only=True)

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
