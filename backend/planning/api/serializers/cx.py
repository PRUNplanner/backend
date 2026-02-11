from api.serializer import PydanticJSONField
from django.db import transaction
from planning.models import PlanningCX
from planning.schemas.latest_schemas import LATEST_SCHEMA
from rest_framework import serializers

from .empire import PlanningEmpireNestedSerializer


class PlanningCXDetailSerializer(serializers.ModelSerializer):
    empires = PlanningEmpireNestedSerializer(many=True, read_only=True)

    cx_data = PydanticJSONField(pydantic_model=LATEST_SCHEMA['CX_DATA'])

    class Meta:
        model = PlanningCX
        exclude = ['created_at', 'modified_at', 'schema_version', 'user']

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


class PlanningCXJunctionsSyncSuccessSerializer(serializers.Serializer):
    status = serializers.CharField(default='CX-Empire junctions updated')
