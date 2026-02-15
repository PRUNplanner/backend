from itertools import chain
from typing import Any, cast

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
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
from pydantic import TypeAdapter, ValidationError
from rest_framework import exceptions, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response


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
                return Response(
                    {'detail': 'No infrastructure reports found for this planet'}, status=status.HTTP_404_NOT_FOUND
                )

            return GamePlanetInfrastructureReportSerializer(latest_report).data

        return GamedataCacheManager.get_planet_latest_popr(planet.planet_natural_id, lambda: fetch_data(planet))


class GameExchangeViewSet(viewsets.GenericViewSet, mixins.ListModelMixin):
    serializer_class = GameExchangeSerializer
    permission_classes = [AllowAny]

    @extend_schema(auth=[], summary='List all exchanges')
    def list(self, request, *args, **kwargs):
        target_exchanges = ['AI1', 'NC1', 'CI1', 'IC1', 'UNIVERSE']

        latest_analytics = (
            GameExchangeAnalytics.objects.filter(exchange_code__in=target_exchanges)
            .order_by('ticker', 'exchange_code', '-date_epoch')
            .distinct('ticker', 'exchange_code')
        )

        def fetch_data() -> Any:
            return self.get_serializer(latest_analytics, many=True).data

        return GamedataCacheManager.get_exchange_list_response(fetch_data)


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
                    'ticker', 'exchange_code', 'date_epoch', 'open_p', 'close_p', 'high_p', 'low_p', 'volume'
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
