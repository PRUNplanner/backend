from api.serializer import PydanticJSONField
from planning.models import PlanningPlanVersion, PlanVersionKind
from planning.schemas.latest_schemas import LATEST_SCHEMA
from rest_framework import serializers

from .plan import PlanningPlanDetailSerializer


class PlanningPlanVersionSerializer(serializers.ModelSerializer):
    # the view passes the newest version as context['active_uuid']
    kind = serializers.ChoiceField(
        choices=[choice for choice in PlanVersionKind.choices if choice[0] != PlanVersionKind.DRAFT], read_only=True
    )
    is_active = serializers.SerializerMethodField()

    class Meta:
        model = PlanningPlanVersion
        fields = ['uuid', 'kind', 'name', 'created_at', 'source_version', 'is_active', 'data_bytes']
        read_only_fields = fields

    def get_is_active(self, version: PlanningPlanVersion) -> bool:
        return version.pk == self.context['active_uuid']


class PlanningPlanVersionDetailSerializer(PlanningPlanVersionSerializer):
    # read as stored: an old snapshot needn't fit the latest schema
    plan_data = PydanticJSONField(pydantic_model=LATEST_SCHEMA['PLANNING_DATA'], read_only=True)

    class Meta(PlanningPlanVersionSerializer.Meta):
        fields = [
            *PlanningPlanVersionSerializer.Meta.fields,
            'plan_name',
            'planet_natural_id',
            'plan_permits_used',
            'plan_cogc',
            'plan_corphq',
            'plan_data',
        ]
        read_only_fields = fields


class PlanningPlanVersionRenameSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=80, allow_blank=True)


class PlanningPlanDraftSerializer(PlanningPlanDetailSerializer):
    # the fields of a plan save, validated as one
    base_modified_at = serializers.DateTimeField(write_only=True)

    class Meta(PlanningPlanDetailSerializer.Meta):
        fields = [
            'plan_name',
            'planet_natural_id',
            'plan_permits_used',
            'plan_cogc',
            'plan_corphq',
            'plan_data',
            'base_modified_at',
        ]


class PlanningPlanDraftSavedSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlanningPlanVersion
        fields = ['updated_at']
        read_only_fields = fields


class PlanningPlanDraftDetailSerializer(serializers.ModelSerializer):
    plan_data = PydanticJSONField(pydantic_model=LATEST_SCHEMA['PLANNING_DATA'], read_only=True)

    class Meta:
        model = PlanningPlanVersion
        fields = [
            'plan_name',
            'planet_natural_id',
            'plan_permits_used',
            'plan_cogc',
            'plan_corphq',
            'plan_data',
            'base_modified_at',
            'updated_at',
        ]
        read_only_fields = fields


class PlanningPlanDraftResponseSerializer(serializers.Serializer):
    draft = PlanningPlanDraftDetailSerializer(allow_null=True)
