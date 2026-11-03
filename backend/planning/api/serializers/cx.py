from api.serializer import PydanticJSONField
from django.db import transaction
from planning.models import PlanningCX
from planning.schemas.latest_schemas import LATEST_SCHEMA
from rest_framework import serializers

from .conflict import SaveConflictMixin
from .empire import PlanningEmpireNestedSerializer


class PlanningCXDetailSerializer(SaveConflictMixin, serializers.ModelSerializer):
    empires = PlanningEmpireNestedSerializer(source='cxs', many=True, read_only=True)

    cx_data = PydanticJSONField(pydantic_model=LATEST_SCHEMA['CX_DATA'])

    class Meta:
        model = PlanningCX
        fields = ['uuid', 'base_modified_at', 'empires', 'cx_data', 'modified_at', 'cx_name']

    @transaction.atomic
    def create(self, validated_data):
        user = self.context['request'].user

        return PlanningCX.objects.create(user=user, **validated_data)


class PlanningCXEmpireJunctionSerializer(serializers.Serializer):
    empire_uuid = serializers.UUIDField()


class PlanningCXJunctionUpdateSerializer(serializers.Serializer):
    cx_uuid = serializers.UUIDField()
    empires = PlanningCXEmpireJunctionSerializer(many=True)


class PlanningCXJunctionsSyncErrorSerializer(serializers.Serializer):
    error = serializers.CharField()
