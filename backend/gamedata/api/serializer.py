from datetime import UTC, date, datetime, time, timedelta

from api.mixins import BlankAsNullMixin
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field, inline_serializer
from gamedata.fio.schemas import drf_sites_schema
from gamedata.models import (
    GameBuilding,
    GameBuildingCost,
    GameBuildingExpertiseChoices,
    GameExchangeAnalytics,
    GameExchangeCXPC,
    GameMaterial,
    GamePlanet,
    GamePlanetCOGCProgram,
    GamePlanetCOGCProgramChoices,
    GamePlanetCurrencyCodeChoices,
    GamePlanetInfrastructureReport,
    GamePlanetResource,
    GameRecipe,
    GameRecipeInput,
    GameRecipeOutput,
)
from rest_framework import serializers

CXPC_EXCHANGES = ['AI1', 'CI1', 'IC1', 'NC1', 'UNIVERSE']

WORKFORCE_ORDER = ['PIONEER', 'SETTLER', 'TECHNICIAN', 'ENGINEER', 'SCIENTIST']
WF_INDEX_MAP = {level: i for i, level in enumerate(WORKFORCE_ORDER)}


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
        exclude = ['standard_recipe_name']

    @extend_schema_field(
        serializers.CharField(
            help_text="Composite ID formatted as 'BUILDING_TICKER#RECIPE_NAME'. Example: 'BMP#100xPE 25xPG=>20xOVE'"
        )
    )
    def get_recipe_id(self, obj: GameRecipe):
        return f'{obj.building_ticker}#{obj.recipe_name}'


class GameBuildingCostSerializer(serializers.ModelSerializer):
    class Meta:
        model = GameBuildingCost
        fields = ['material_ticker', 'material_amount']


class GameBuildingSerializer(BlankAsNullMixin, serializers.ModelSerializer):
    blank_as_null_fields = ('expertise',)
    costs = GameBuildingCostSerializer(many=True, read_only=True)
    habitations = serializers.ReadOnlyField()

    class Meta:
        model = GameBuilding
        exclude = ['building_id']


class GameBuildingCSVSerializer(serializers.ModelSerializer):
    class Meta:
        model = GameBuilding
        fields = [
            'building_ticker',
            'building_name',
            'building_type',
            'expertise',
            'pioneers',
            'settlers',
            'technicians',
            'engineers',
            'scientists',
            'area_cost',
        ]


class GamePlanetResourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = GamePlanetResource
        fields = ['resource_type', 'factor', 'daily_extraction', 'material_ticker', 'max_daily_extraction']


class GamePlanetCOGCProgramSerializer(serializers.ModelSerializer):
    class Meta:
        model = GamePlanetCOGCProgram
        fields = ['program_type', 'start_epochms', 'end_epochms']


class GamePlanetSerializer(BlankAsNullMixin, serializers.ModelSerializer):
    blank_as_null_fields = ('faction_code', 'faction_name', 'cogc_program_status')
    resources = GamePlanetResourceSerializer(many=True, read_only=True)
    cogc_programs = GamePlanetCOGCProgramSerializer(many=True, read_only=True)

    active_cogc_program_type = serializers.CharField(read_only=True)

    production_fees = serializers.SerializerMethodField()

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
            'production_fees',
            *GamePlanet.PLOT_FIELDS,
        ]

    @extend_schema_field(
        inline_serializer(
            name='PlanetProductionFeeSchema',
            fields={
                'currency': serializers.ChoiceField(choices=GamePlanetCurrencyCodeChoices.choices),
                'fees': serializers.DictField(
                    child=serializers.ListField(
                        child=serializers.FloatField(),
                        min_length=5,
                        max_length=5,
                        help_text='[PIONEER, SETTLER, TECHNICIAN, ENGINEER, SCIENTIST]',
                    ),
                    help_text=(
                        'Dictionary keys are Building Categories (GameBuildingExpertiseChoices). '
                        'Valid keys include: '
                        + ', '.join([choice[0] for choice in GameBuildingExpertiseChoices.choices])
                    ),
                ),
            },
        )
    )
    def get_production_fees(self, obj: GamePlanet) -> dict[str, object] | None:

        # prefetched
        fees = obj.production_fees.all()
        if not fees:
            return None

        fee_map: dict[str, list[float]] = {}
        for fee in fees:
            cat = fee.category
            if cat not in fee_map:
                # init with 0.0 for all workforces
                fee_map[cat] = [0.0] * len(WORKFORCE_ORDER)

            try:
                # overwrite with the values from data
                idx = WF_INDEX_MAP[fee.workforce_level]
                fee_map[cat][idx] = fee.fee_amount
            except KeyError:
                continue

        return {'currency': obj.currency_code, 'fees': fee_map}


class RoundedFloatField(serializers.FloatField):
    """Four decimals: keeps the search index's payload about 20% smaller without a visible difference."""

    def to_representation(self, value: float) -> float:
        return round(super().to_representation(value), 4)


class PlanetSearchIndexResourceSerializer(serializers.ModelSerializer):
    daily_extraction = RoundedFloatField(read_only=True)
    max_daily_extraction = RoundedFloatField(read_only=True)

    class Meta:
        model = GamePlanetResource
        fields = ['material_ticker', 'resource_type', 'daily_extraction', 'max_daily_extraction']


class PlanetSearchIndexSerializer(BlankAsNullMixin, serializers.ModelSerializer):
    """Slim planet shape for the client-side planet search; cogc_programs are expected prefetched unexpired."""

    blank_as_null_fields = ('cogc_program_status',)
    fertility = RoundedFloatField(read_only=True)
    resources = PlanetSearchIndexResourceSerializer(many=True, read_only=True)
    cogc_programs = GamePlanetCOGCProgramSerializer(many=True, read_only=True)

    class Meta:
        model = GamePlanet
        fields = [
            'planet_natural_id',
            'planet_name',
            'system_id',
            'surface',
            'gravity_type',
            'pressure_type',
            'temperature_type',
            'fertility',
            'has_localmarket',
            'has_chamberofcommerce',
            'has_warehouse',
            'has_administrationcenter',
            'has_shipyard',
            'cogc_program_status',
            'cogc_programs',
            'resources',
            *GamePlanet.PLOT_FIELDS,
        ]


class PlanetIdsSerializer(serializers.ListSerializer):
    child = serializers.CharField(min_length=7, max_length=7)

    def __init__(self, *args: object, **kwargs: object) -> None:
        kwargs.setdefault('max_length', 200)
        super().__init__(*args, **kwargs)


class GameExchangeSerializer(serializers.ModelSerializer):
    ticker_id = serializers.CharField(read_only=True)
    exchange_status = serializers.CharField(read_only=True)

    # live data fields

    ask = serializers.FloatField(read_only=True, allow_null=True)
    bid = serializers.FloatField(read_only=True, allow_null=True)
    supply = serializers.FloatField(read_only=True, allow_null=True)
    demand = serializers.FloatField(read_only=True, allow_null=True)

    class Meta:
        model = GameExchangeAnalytics
        fields = [
            'ticker',
            'exchange_code',
            'calendar_date',
            'ticker_id',
            'exchange_status',
            'traded_daily',
            'vwap_daily',
            'sum_traded_7d',
            'avg_traded_7d',
            'vwap_7d',
            'sum_traded_30d',
            'avg_traded_30d',
            'vwap_30d',
            'ask',
            'bid',
            'supply',
            'demand',
        ]


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
            site_lookup.get(str(getattr(m, id_field)), f'Unknown_Site_{getattr(m, id_field)}'): {
                **m.to_game_storage_dict(),
                'Identifier': site_lookup.get(str(getattr(m, id_field)), f'Unknown_Site_{getattr(m, id_field)}'),
            }
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


class CXPCSparklinesQuerySerializer(serializers.Serializer):
    MAX_TICKERS = 50

    days = serializers.IntegerField(min_value=1, max_value=90, default=30)
    metric = serializers.ChoiceField(
        choices=['vwap_daily', 'vwap_7d'],
        default='vwap_daily',
        help_text='vwap_daily: VWAP of the day, up to today; vwap_7d: trailing 7 days VWAP, up to yesterday',
    )
    tickers = serializers.CharField(
        required=False, help_text=f'Comma separated, at most {MAX_TICKERS}; unknown tickers are ignored'
    )

    def validate_tickers(self, value: str) -> list[str]:
        tickers = sorted({ticker.strip() for ticker in value.split(',') if ticker.strip()})
        if len(tickers) > self.MAX_TICKERS:
            raise serializers.ValidationError(f'At most {self.MAX_TICKERS} tickers.')
        return tickers


class CXPCDailyQuerySerializer(serializers.Serializer):
    """A range of whole UTC days, `since` to `until` or the last `days`, capped so one response stays ~10k rows."""

    MAX_DAYS_ONE_EXCHANGE = 31
    MAX_DAYS_ALL_EXCHANGES = 7

    since = serializers.DateField(required=False, help_text='First day (UTC), inclusive. Either since or days.')
    until = serializers.DateField(required=False, help_text='Last day (UTC), inclusive; with since, default today')
    days = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=MAX_DAYS_ONE_EXCHANGE,
        help_text='The last N days up to today (UTC). Either since or days.',
    )
    exchange = serializers.ChoiceField(choices=CXPC_EXCHANGES, required=False, help_text='Default all exchanges')

    def validate(self, attrs: dict) -> dict:
        today = timezone.now().astimezone(UTC).date()
        days: int | None = attrs.get('days')
        since: date | None = attrs.get('since')
        if days is not None:
            if since is not None:
                raise serializers.ValidationError({'since': 'Give either since or days.'})
            if 'until' in attrs:
                raise serializers.ValidationError({'until': 'Only with since.'})
            since = today - timedelta(days=days - 1)
        elif since is None:
            raise serializers.ValidationError({'since': 'Give either since or days.'})

        until: date = attrs.get('until', today)
        if until < since:
            raise serializers.ValidationError({'until': 'Must not be before since.'})

        exchange: str | None = attrs.get('exchange')
        cap = self.MAX_DAYS_ONE_EXCHANGE if exchange else self.MAX_DAYS_ALL_EXCHANGES
        if (until - since).days + 1 > cap:
            field = 'days' if days is not None else 'until'
            raise serializers.ValidationError({field: f'At most {cap} days{"" if exchange else " without exchange"}.'})

        return {'since': since, 'until': until, 'exchange': exchange}

    @staticmethod
    def epoch(day: date) -> int:
        """00:00 UTC of a day in epoch ms, as CXPC date_epoch."""
        return int(datetime.combine(day, time(), tzinfo=UTC).timestamp() * 1000)


class CXPCSparklinesSerializer(serializers.Serializer):
    exchange = serializers.CharField()
    days = serializers.IntegerField()
    series = serializers.DictField(
        child=serializers.ListField(child=serializers.FloatField(allow_null=True)),
        help_text='VWAP per ticker, oldest first, null without trades: daily up to today, or vwap_7d up to yesterday',
    )


def _day_series(help_text: str) -> serializers.ListField:
    return serializers.ListField(child=serializers.FloatField(allow_null=True), help_text=help_text)


class MarketSummaryIndexSerializer(serializers.Serializer):
    base = serializers.IntegerField(help_text='Epoch ms of day 0, where every index is 100')
    combined = _day_series('ALL exchanges, one decimal, oldest first')
    per_exchange = serializers.DictField(child=_day_series('Index of one exchange'))
    per_sector = serializers.DictField(child=_day_series('Index of one sector, on ALL prices'))
    members = serializers.DictField(child=serializers.IntegerField(), help_text='Members per ALL, exchange and sector')
    coverage = serializers.DictField(
        child=serializers.FloatField(), help_text="Members' share of the 30-day turnover, per ALL and exchange"
    )


class MarketSummarySectorSerializer(serializers.Serializer):
    key = serializers.CharField()
    categories = serializers.ListField(child=serializers.CharField())


class MarketSummaryMaterialSerializer(serializers.Serializer):
    ticker = serializers.CharField()
    name = serializers.CharField(help_text='Material name, empty for a ticker missing from the materials')
    category = serializers.CharField()
    sector = serializers.CharField()
    turnover_30d = serializers.FloatField(help_text='Whole ȼ, 4 significant digits')
    turnover_7d = serializers.FloatField(help_text='Whole ȼ, 4 significant digits')
    units_7d = serializers.IntegerField()
    vwap_7d = serializers.FloatField(allow_null=True)
    vwap_30d = serializers.FloatField(allow_null=True)
    change_pct = serializers.FloatField(allow_null=True, help_text='vwap_7d / vwap_30d - 1, in %')
    spark = _day_series('ALL VWAP 7D of the last 15 days, oldest first, 4 significant digits')
    is_flat = serializers.BooleanField(help_text='Held at one price, e.g. by market makers')
    turnover_7d_by_exchange = serializers.DictField(
        child=serializers.FloatField(),
        help_text='Whole ȼ, 3 significant digits; exchanges without trades in 7 days are left out',
    )


class MarketSummarySerializer(serializers.Serializer):
    as_of = serializers.IntegerField(help_text='Epoch ms of the newest complete UTC day')
    start = serializers.IntegerField(help_text='Epoch ms of the first day')
    days = serializers.IntegerField()
    turnover_daily = serializers.DictField(
        child=_day_series('Turnover per day in ȼ, null without trades'), help_text='Per exchange'
    )
    index = MarketSummaryIndexSerializer()
    sectors = MarketSummarySectorSerializer(many=True)
    materials = MarketSummaryMaterialSerializer(many=True, help_text='Tickers traded in the last 30 days')


class GamePlanetInfrastructureReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = GamePlanetInfrastructureReport
        exclude = ['id', 'planet', 'infrastructure_report_id']
