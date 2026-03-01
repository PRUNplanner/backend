from datetime import timedelta
from itertools import chain
from typing import Any, cast

from django.db.models import Case, CharField, F, Q, Value, When
from django.db.models.functions import Concat
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, extend_schema
from gamedata.api.serializer import (
    GameBuildingSerializer,
    GameExchangeCXPCSerializer,
    GameExchangeSerializer,
    GameMaterialSerializer,
    GamePlanetInfrastructureReportSerializer,
    GamePlanetSerializer,
    GameRecipeSerializer,
    GameStorageSerializer,
    PlanetIdsSerializer,
    PlanetSearchSerializer,
)
from gamedata.fio.schemas import (
    FIOUserShipSiteSchema,
    FIOUserSiteSchema,
    FIOUserSiteWarehouseSchema,
    FIOUserStorageSchema,
)
from gamedata.gamedata_cache_manager import GamedataCacheManager
from gamedata.models import (
    GameBuilding,
    GameExchangeAnalytics,
    GameExchangeCXPC,
    GameFIOPlayerData,
    GameMaterial,
    GamePlanet,
    GameRecipe,
    queryset_gameplanet,
)
from gamedata.services.planet_search import GamePlanetSearchService
from pydantic import TypeAdapter
from rest_framework import exceptions, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework_csv.renderers import CSVRenderer


class GameRecipeViewSet(viewsets.GenericViewSet, mixins.ListModelMixin):
    queryset = GameRecipe.objects.prefetch_related('inputs', 'outputs')
    serializer_class = GameRecipeSerializer
    permission_classes = [AllowAny]

    @extend_schema(auth=[], summary='List all recipes')
    def list(self, request, *args, **kwargs):
        def fetch_data() -> Any:
            return self.get_serializer(self.get_queryset(), many=True).data

        return GamedataCacheManager.get_recipe_list_response(fetch_data)


class GameMaterialViewSet(viewsets.GenericViewSet, mixins.ListModelMixin):
    queryset = GameMaterial.objects.all()
    serializer_class = GameMaterialSerializer
    permission_classes = [AllowAny]

    @extend_schema(auth=[], summary='List all materials')
    def list(self, request, *args, **kwargs):
        def fetch_data() -> Any:
            return self.get_serializer(self.get_queryset(), many=True).data

        return GamedataCacheManager.get_material_list_response(fetch_data)


class GameBuildingViewSet(viewsets.GenericViewSet, mixins.ListModelMixin):
    queryset = GameBuilding.objects.prefetch_related('costs')
    serializer_class = GameBuildingSerializer

    @extend_schema(auth=[], summary='List all buildings')
    def list(self, request, *args, **kwargs):
        def fetch_data() -> Any:
            return self.get_serializer(self.get_queryset(), many=True).data

        return GamedataCacheManager.get_building_list_response(fetch_data)


class GamePlanetViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [AllowAny]
    queryset = queryset_gameplanet()
    serializer_class = GamePlanetSerializer
    lookup_field = 'planet_natural_id'

    @extend_schema(auth=[], summary='List all planets')
    def list(self, request, *args, **kwargs):
        def fetch_data() -> Any:
            return self.get_serializer(self.get_queryset(), many=True).data

        return GamedataCacheManager.get_planet_list_response(fetch_data)

    @extend_schema(auth=[], summary='Fetch a single planet by its Planet Natural Id')
    def retrieve(self, request, *args, **kwargs):
        planet_natural_id: str = cast(str, kwargs.get('planet_natural_id'))

        def fetch_data() -> Any:
            planet = get_object_or_404(self.get_queryset(), planet_natural_id=planet_natural_id)
            return self.get_serializer(planet).data

        return GamedataCacheManager.get_planet_get_response(planet_natural_id, fetch_data)

    @extend_schema(auth=[], summary='Search a single planet by its Planet Natural Id or Name')
    def search_single(self, request, *args, **kwargs):
        search_term: str = cast(str, kwargs.get('search_term'))

        if not search_term or len(search_term.strip()) < 3:
            raise ValidationError({'search_term': 'Search term must be at least 3 characters long.'})

        def fetch_data(search_term: str):
            result = GamePlanetSearchService.search_by_term(search_term)
            return self.get_serializer(result, many=True).data

        return GamedataCacheManager.get_planet_searchterm(search_term, lambda: fetch_data(search_term))

    @extend_schema(
        auth=[],
        summary='Fetch multiple planets by their Planet Natural Ids',
        request=PlanetIdsSerializer,
        responses=GamePlanetSerializer(many=True),
    )
    def multiple(self, request: Request):
        serializer = PlanetIdsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ids = serializer.validated_data

        def fetch_data(ids: list[str]) -> Any:
            result = GamePlanetSearchService.search_by_planet_natural_id(ids)
            return self.get_serializer(result, many=True).data

        return GamedataCacheManager.get_planet_multiple_response(ids, lambda: fetch_data(ids))

    @extend_schema(
        auth=[],
        request=PlanetSearchSerializer,
        responses=GamePlanetSerializer(many=True),
        summary='Search for planets by various parameters',
    )
    def search(self, request: Request):
        serializer = PlanetSearchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        def fetch_data(data: Any) -> Any:
            result = GamePlanetSearchService.search(data)
            return GamePlanetSerializer(result, many=True).data

        return GamedataCacheManager.get_planet_search_response(data, lambda: fetch_data(data))

    @extend_schema(
        auth=[],
        responses=GamePlanetInfrastructureReportSerializer,
        summary='Get planets latest population report',
    )
    def latest_popr(self, request, planet_natural_id=None):

        planet = get_object_or_404(GamePlanet, planet_natural_id=planet_natural_id)

        def fetch_data(planet: GamePlanet):
            latest_report = planet.popr_reports.all().first()

            if not latest_report:
                raise NotFound('No infrastructure reports found for this planet')

            return GamePlanetInfrastructureReportSerializer(latest_report).data

        return GamedataCacheManager.get_planet_latest_popr(planet.planet_natural_id, lambda: fetch_data(planet))


class GameExchangeViewSet(viewsets.GenericViewSet, mixins.ListModelMixin):
    serializer_class = GameExchangeSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        target_exchanges = ['AI1', 'NC1', 'CI1', 'IC1', 'UNIVERSE']
        two_days_ago = timezone.now().date() - timedelta(days=2)

        return (
            GameExchangeAnalytics.objects.filter(exchange_code__in=target_exchanges)
            .annotate(
                ticker_id=Concat(F('ticker'), Value('.'), F('exchange_code'), output_field=CharField()),
                exchange_status=Case(
                    When(calendar_date__lt=two_days_ago, then=Value('STALE')),
                    When(Q(vwap_7d__gt=0) & Q(avg_traded_7d__gt=0), then=Value('ACTIVE')),
                    default=Value('INACTIVE'),
                    output_field=CharField(),
                ),
            )
            .order_by('ticker', 'exchange_code', '-date_epoch')
            .distinct('ticker', 'exchange_code')
        )

    @extend_schema(auth=[], summary='List all exchanges')
    def list(self, request, *args, **kwargs):

        fmt = getattr(request.accepted_renderer, 'format', 'json')

        def fetch_data() -> Any:
            return list(
                self.get_queryset().values(
                    'ticker',
                    'exchange_code',
                    'date_epoch',
                    'calendar_date',
                    'traded_daily',
                    'vwap_daily',
                    'sum_traded_7d',
                    'avg_traded_7d',
                    'vwap_7d',
                    'sum_traded_30d',
                    'avg_traded_30d',
                    'vwap_30d',
                    'ticker_id',
                    'exchange_status',
                )
            )

        return GamedataCacheManager.get_exchange_list_response(fetch_data, fmt)


@extend_schema(
    tags=['data : csv'],
    summary='List all exchanges (CSV)',
    responses={
        (200, 'text/csv'): OpenApiTypes.STR,
    },
    examples=[
        OpenApiExample(
            'CSV Example',
            summary='Example of the CSV output',
            value=(
                'ticker,exchange_code,ticker_id,date_epoch,calendar_date,exchange_status,vwap_daily,vwap_7d,vwap_30d,'
                'traded_daily,sum_traded_7d,sum_traded_30d,avg_traded_7d,avg_traded_30d\n'
                'AAR,AI1,AAR.AI1,1772323200000,2026-03-01,ACTIVE,0.0,16100.0,16077.0,0.0,16.0,200.0,'
                '2.6666666666666665,11.764705882352942\n'
                'BBH,NC1,BBH.NC1,1772323200000,2026-03-01,ACTIVE,2900.0,2891.514476614699,2215.1506948034653,63.0,'
                '898.0,63831.0,128.28571428571428,2127.7\n'
                'OVE,AI1,OVE.AI1,1772323200000,2026-03-01,ACTIVE,128.66921119592877,128.372457796092,'
                '127.6020758545341,1572.0,7523.0,51256.0,1074.7142857142858,1708.5333333333333\n'
            ),
            media_type='text/csv',
        )
    ],
)
class GameExchangeCSVViewSet(GameExchangeViewSet):
    renderer_classes = [CSVRenderer]

    header = [
        'ticker',
        'exchange_code',
        'ticker_id',
        'date_epoch',
        'calendar_date',
        'exchange_status',
        'vwap_daily',
        'vwap_7d',
        'vwap_30d',
        'traded_daily',
        'sum_traded_7d',
        'sum_traded_30d',
        'avg_traded_7d',
        'avg_traded_30d',
    ]

    # override viewsets header order with header variable
    def get_renderer_context(self):
        context = super().get_renderer_context()
        context['header'] = self.header
        return context


class GameStorageViewSet(viewsets.GenericViewSet, mixins.RetrieveModelMixin):
    serializer_class = GameStorageSerializer
    permission_classes = [IsAuthenticated]

    @extend_schema(summary='Retrieve users storage data')
    def retrieve(self, request, *ars, **kwargs):
        def fetch_data():
            def _build_site_map(sites, warehouses, ships) -> dict:
                return dict(item.map_entry for item in chain(sites, warehouses, ships))

            data_object = GameFIOPlayerData.objects.filter(user=request.user).first()

            # user must have data
            if not data_object:
                return Response(status=status.HTTP_404_NOT_FOUND)

            # validate schemas of stored data
            try:
                sites_data = TypeAdapter(list[FIOUserSiteSchema]).validate_python(data_object.site_data)
                storage_data = TypeAdapter(list[FIOUserStorageSchema]).validate_python(data_object.storage_data)
                warehouse_data = TypeAdapter(list[FIOUserSiteWarehouseSchema]).validate_python(
                    data_object.warehouse_data
                )
                ship_data = TypeAdapter(list[FIOUserShipSiteSchema]).validate_python(data_object.ship_data)
            except ValidationError as err:
                raise exceptions.ValidationError() from err

            response_data = {
                'site_map': _build_site_map(sites_data, warehouse_data, ship_data),
                'pydantic_sites_data': sites_data,
                'pydantic_storage_data': storage_data,
                'last_modified': storage_data[0].Timestamp if storage_data else None,
            }

            # serialize
            serializer = self.get_serializer(response_data)
            return serializer.data

        return GamedataCacheManager.get_storage_response(request.user.id, fetch_data)


class ExchangeCXPCViewSet(viewsets.ReadOnlyModelViewSet):
    ALLOWED_EXCHANGES = ['AI1', 'CI1', 'IC1', 'NC1', 'UNIVERSE']

    queryset = GameExchangeCXPC.objects.all()
    permission_classes = [AllowAny]

    def _get_cxpc_response(self, ticker, exchange_code=None):
        def fetch_data():
            qs = self.get_queryset().filter(ticker=ticker)
            if exchange_code:
                qs = qs.filter(exchange_code=exchange_code)
            else:
                qs = qs.filter(exchange_code__in=self.ALLOWED_EXCHANGES)

            return list(
                qs.order_by('-date_epoch').values(
                    'ticker', 'exchange_code', 'date_epoch', 'open_p', 'close_p', 'high_p', 'low_p', 'volume', 'traded'
                )
            )

        return GamedataCacheManager.get_exchange_cxpc_response(ticker, exchange_code, fetch_data)

    @extend_schema(
        auth=[],
        summary='Get ticker CXPC data (All Exchanges)',
        operation_id='exchange_cxpc_all',
        responses={200: GameExchangeCXPCSerializer(many=True)},
    )
    @action(detail=False, url_path='market_data/(?P<ticker>[^/.]+)')
    def cxpc_ticker_data(self, request, ticker):
        return self._get_cxpc_response(ticker)

    @extend_schema(
        auth=[],
        summary='Get ticker CXPC data for specific Exchange',
        operation_id='exchange_cxpc_specific',
        responses={200: GameExchangeCXPCSerializer(many=True)},
    )
    @action(detail=False, url_path='market_data/(?P<ticker>[^/.]+)/(?P<exchange_code>[^/.]+)')
    def cxpc_exchange_data(self, request, ticker, exchange_code):
        if exchange_code not in self.ALLOWED_EXCHANGES:
            return Response(
                {'error': f'Invalid exchange code. Supported: {", ".join(self.ALLOWED_EXCHANGES)}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return self._get_cxpc_response(ticker, exchange_code)
