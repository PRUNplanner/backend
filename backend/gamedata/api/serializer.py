from drf_spectacular.utils import extend_schema_field, inline_serializer
from gamedata.fio.schemas import drf_sites_schema
from gamedata.models import (
    GameBuilding,
    GameBuildingCost,
    GameExchangeAnalytics,
    GameExchangeCXPC,
    GameMaterial,
    GamePlanet,
    GamePlanetCOGCProgram,
    GamePlanetCOGCProgramChoices,
    GamePlanetInfrastructureReport,
    GamePlanetResource,
    GameRecipe,
    GameRecipeInput,
    GameRecipeOutput,
)
from rest_framework import serializers


class GameMaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = GameMaterial
        fields = '__all__'


class GameRecipeInputSerializer(serializers.ModelSerializer):
    class Meta:
        model = GameRecipeInput
        fields = ['material_ticker', 'material_amount']


class GameRecipeOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = GameRecipeOutput
        fields = ['material_ticker', 'material_amount']


class GameRecipeSerializer(serializers.ModelSerializer):
    inputs = GameRecipeInputSerializer(many=True, read_only=True)
    outputs = GameRecipeOutputSerializer(many=True, read_only=True)

    recipe_id = serializers.SerializerMethodField()

    class Meta:
        model = GameRecipe
        fields = '__all__'

    def get_recipe_id(self, obj: GameRecipe):
        return f'{obj.building_ticker}#{obj.recipe_name}'


class GameBuildingCostSerializer(serializers.ModelSerializer):
    class Meta:
        model = GameBuildingCost
        fields = ['material_ticker', 'material_amount']


class GameBuildingSerializer(serializers.ModelSerializer):
    costs = GameBuildingCostSerializer(many=True, read_only=True)
    habitations = serializers.ReadOnlyField()

    class Meta:
        model = GameBuilding
        fields = '__all__'


class GamePlanetResourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = GamePlanetResource
        fields = ['resource_type', 'factor', 'daily_extraction', 'material_ticker']


class GamePlanetCOGCProgramSerializer(serializers.ModelSerializer):
    class Meta:
        model = GamePlanetCOGCProgram
        fields = ['program_type', 'start_epochms', 'end_epochms']


class GamePlanetSerializer(serializers.ModelSerializer):
    resources = GamePlanetResourceSerializer(many=True, read_only=True)
    cogc_programs = GamePlanetCOGCProgramSerializer(many=True, read_only=True)

    active_cogc_program_type = serializers.CharField(read_only=True)

    class Meta:
        model = GamePlanet
        fields = [
            'planet_id',
            'planet_natural_id',
            'planet_name',
            'system_id',
            'has_localmarket',
            'has_chamberofcommerce',
            'has_warehouse',
            'has_administrationcenter',
            'has_shipyard',
            'pressure',
            'surface',
            'gravity',
            'temperature',
            'fertility',
            'faction_code',
            'faction_name',
            'cogc_program_status',
            'resources',
            'cogc_programs',
            'active_cogc_program_type',
        ]


class PlanetIdsSerializer(serializers.ListSerializer):
    child = serializers.CharField(min_length=7, max_length=7)


class GameExchangeSerializer(serializers.ModelSerializer):
    ticker_id = serializers.SerializerMethodField()

    class Meta:
        model = GameExchangeAnalytics
        exclude = ['id', 'date_epoch']

    def get_ticker_id(self, obj):
        return f'{obj.ticker}.{obj.exchange_code}'


class PlanetSearchSerializer(serializers.Serializer):
    materials = serializers.ListSerializer(child=serializers.CharField(min_length=1, max_length=3))
    cogc_programs = serializers.ListSerializer(
        child=serializers.ChoiceField(choices=GamePlanetCOGCProgramChoices.choices)
    )
    must_be_fertile = serializers.BooleanField()

    environment_rocky = serializers.BooleanField()
    environment_gaseous = serializers.BooleanField()
    environment_low_gravity = serializers.BooleanField()
    environment_high_gravity = serializers.BooleanField()
    environment_low_pressure = serializers.BooleanField()
    environment_high_pressure = serializers.BooleanField()
    environment_low_temperature = serializers.BooleanField()
    environment_high_temperature = serializers.BooleanField()

    must_have_localmarket = serializers.BooleanField()
    must_have_chamberofcommerce = serializers.BooleanField()
    must_have_warehouse = serializers.BooleanField()
    must_have_administrationcenter = serializers.BooleanField()
    must_have_shipyard = serializers.BooleanField()


class GameStorageSerializer(serializers.Serializer):
    """
    Structure:
        sites_data: [natural identifier]: sites data
        storage_data:
            planets (STORE)
            warehouses (WAREHOUSE_STORE)
            ships (SHIP_STORE)
            - all dicts via [natural identifier]: storage data
        last_modified: storage last update
    """

    storage_data = serializers.SerializerMethodField()
    sites_data = serializers.SerializerMethodField()
    last_modified = serializers.DateTimeField()

    def _map_storage_by_type(self, obj, storage_type: str, id_field: str = 'StorageId'):
        site_lookup = obj.get('site_map', {})
        items = obj.get('pydantic_storage_data', [])

        return {
            site_lookup.get(str(getattr(m, id_field)), f'Unknown_Site_{getattr(m, id_field)}'): m.to_game_storage_dict()
            for m in items
            if m.Type == storage_type
        }

    @extend_schema_field(
        inline_serializer(
            name='StorageDataMap',
            fields={
                'planets': serializers.DictField(child=serializers.DictField()),
                'warehouses': serializers.DictField(child=serializers.DictField()),
                'ships': serializers.DictField(child=serializers.DictField()),
            },
        )
    )
    def get_storage_data(self, obj):
        # Note: Planets use AddressableId, others use StorageId
        return {
            'planets': self._map_storage_by_type(obj, 'STORE', 'AddressableId'),
            'warehouses': self._map_storage_by_type(obj, 'WAREHOUSE_STORE'),
            'ships': self._map_storage_by_type(obj, 'SHIP_STORE'),
        }

    @extend_schema_field(serializers.DictField(child=drf_sites_schema))
    def get_sites_data(self, obj):
        site_lookup = obj.get('site_map', {})

        return {
            site_lookup.get(str(site.SiteId), f'Unknown_Site_{site.SiteId}'): site.to_game_storage_dict()
            for site in obj.get('pydantic_sites_data', [])
        }


class GameExchangeCXPCSerializer(serializers.ModelSerializer):
    class Meta:
        model = GameExchangeCXPC
        fields = ['ticker', 'exchange_code', 'date_epoch', 'open_p', 'close_p', 'high_p', 'low_p', 'volume', 'traded']
        read_only_fields = fields


class GamePlanetInfrastructureReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = GamePlanetInfrastructureReport
        exclude = ['id', 'planet', 'infrastructure_report_id']
