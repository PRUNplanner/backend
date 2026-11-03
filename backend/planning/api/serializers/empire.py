from django.db import transaction
from django.utils import timezone
from gamedata.models import GameMaterial
from planning.models import PlanningCOGCChoices, PlanningEmpire, PlanningFactionChoices
from rest_framework import serializers

from .conflict import SaveConflictMixin
from .minimal import PlanningCXMinimal, PlanningPlanMinimalSerializer


class PlanningEmpireNestedSerializer(serializers.ModelSerializer):
    plans = PlanningPlanMinimalSerializer(many=True, read_only=True)

    class Meta:
        model = PlanningEmpire
        fields = ['uuid', 'empire_name', 'plans']


class PlanningEmpireListSerializer(serializers.ModelSerializer):
    cx = PlanningCXMinimal(read_only=True, allow_null=True)
    # configuration version: state syncs and cx-junctions don't change it
    modified_at = serializers.DateTimeField(source='config_modified_at', read_only=True)

    class Meta:
        model = PlanningEmpire
        fields = [
            'uuid',
            'empire_name',
            'empire_faction',
            'empire_permits_used',
            'empire_permits_total',
            'cx',
            'modified_at',
        ]


class PlanningEmpireDetailSerializer(SaveConflictMixin, serializers.ModelSerializer):
    plans = PlanningPlanMinimalSerializer(many=True, read_only=True)
    cx = PlanningCXMinimal(read_only=True, allow_null=True)
    # the model's modified_at moves on every state sync, so the configuration has its own version
    modified_at = serializers.DateTimeField(source='config_modified_at', read_only=True)

    version_field = 'config_modified_at'

    class Meta:
        model = PlanningEmpire
        fields = [
            'uuid',
            'base_modified_at',
            'plans',
            'cx',
            'modified_at',
            'empire_name',
            'empire_faction',
            'empire_permits_used',
            'empire_permits_total',
            'needs_state_sync',
        ]
        # set by the state sync, never by the client
        read_only_fields = ['needs_state_sync']

    @transaction.atomic
    def update(self, instance, validated_data):
        instance.config_modified_at = timezone.now()
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


# per day, far above any real empire; FloatField already rejects NaN and inf
MAX_MATERIAL_IO = 1e9


class PlanningEmpireMaterialIOSerializer(serializers.Serializer):
    p = serializers.FloatField(min_value=-MAX_MATERIAL_IO, max_value=MAX_MATERIAL_IO)
    c = serializers.FloatField(min_value=-MAX_MATERIAL_IO, max_value=MAX_MATERIAL_IO)
    d = serializers.FloatField(min_value=-MAX_MATERIAL_IO, max_value=MAX_MATERIAL_IO)


class PlanningEmpireMetadataSerializer(serializers.Serializer):
    faction = serializers.ChoiceField(choices=PlanningFactionChoices.choices)
    permits_used = serializers.IntegerField(min_value=0)
    permits_total = serializers.IntegerField(min_value=0)
    plan_count = serializers.IntegerField(min_value=0)
    timestamp = serializers.CharField()


class PlanningEmpirePlanMetadataSerializer(serializers.Serializer):
    planet_natural_id = serializers.CharField()
    cogc = serializers.ChoiceField(choices=PlanningCOGCChoices.choices)


class SinglePlanDetailSerializer(serializers.Serializer):
    metadata = PlanningEmpirePlanMetadataSerializer()
    deltas = serializers.DictField(
        child=PlanningEmpireMaterialIOSerializer(), help_text='Map of material tickers to their P/C/D values'
    )


class PlanningEmpireStateUpdateSerializer(serializers.Serializer):
    metadata = PlanningEmpireMetadataSerializer()
    empire_total = serializers.DictField(child=PlanningEmpireMaterialIOSerializer())
    plan_details = serializers.DictField(child=SinglePlanDetailSerializer())

    def validate(self, attrs: dict) -> dict:
        # empire_total feeds the public material tracker: known materials only
        tickers = set(attrs['empire_total'])
        for detail in attrs['plan_details'].values():
            tickers.update(detail['deltas'])
        unknown = tickers - set(GameMaterial.objects.filter(ticker__in=tickers).values_list('ticker', flat=True))
        if unknown:
            raise serializers.ValidationError(f'Unknown material tickers: {", ".join(sorted(unknown)[:20])}')
        return attrs

    def update(self, instance, validated_data):
        return instance

    def create(self, validated_data):
        raise NotImplementedError('Use the main Empire serializer for creation.')


class PlanningEmpireStateRequestSerializer(PlanningEmpireStateUpdateSerializer):
    """Docs only: the state PATCH validates without partial, so every field is required."""

    @property
    def partial(self) -> bool:
        return False

    @partial.setter
    def partial(self, value: bool) -> None:
        # drf-spectacular marks every PATCH body partial
        pass


class PlanningEmpireMaterialIOExportRowSerializer(serializers.Serializer):
    ticker = serializers.CharField()
    material_name = serializers.CharField(allow_null=True)
    production = serializers.FloatField(help_text='Per day')
    consumption = serializers.FloatField(help_text='Per day')
    delta = serializers.FloatField(help_text='Per day, production - consumption')
    updated_at = serializers.CharField(allow_null=True, help_text='When the empire state was last saved')


class PlanningEmpireMaterialIOExportSerializer(serializers.Serializer):
    updated_at = serializers.CharField(allow_null=True, help_text='When the empire state was last saved')
    rows = PlanningEmpireMaterialIOExportRowSerializer(many=True)


class PlanningEmpirePlanMaterialIOExportRowSerializer(serializers.Serializer):
    plan_uuid = serializers.UUIDField()
    plan_name = serializers.CharField()
    planet_natural_id = serializers.CharField(allow_null=True)
    cogc = serializers.CharField(allow_null=True)
    ticker = serializers.CharField()
    production = serializers.FloatField(help_text='Per day')
    consumption = serializers.FloatField(help_text='Per day')
    delta = serializers.FloatField(help_text='Per day, production - consumption')
    updated_at = serializers.CharField(allow_null=True, help_text='When the empire state was last saved')


class PlanningEmpirePlanMaterialIOExportSerializer(serializers.Serializer):
    updated_at = serializers.CharField(allow_null=True, help_text='When the empire state was last saved')
    rows = PlanningEmpirePlanMaterialIOExportRowSerializer(many=True)
