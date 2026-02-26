from django.urls import path

from .viewsets import (
    ExchangeCXPCViewSet,
    GameBuildingViewSet,
    GameExchangeViewSet,
    GameMaterialViewSet,
    GamePlanetViewSet,
    GameRecipeViewSet,
    GameStorageViewSet,
)

app_name = 'data'

urlpatterns = [
    path('materials/', GameMaterialViewSet.as_view(actions={'get': 'list'}), name='material-list'),
    path('recipes/', GameRecipeViewSet.as_view(actions={'get': 'list'}), name='recipe-list'),
    path('buildings/', GameBuildingViewSet.as_view(actions={'get': 'list'}), name='building-list'),
    path('planets/multiple', GamePlanetViewSet.as_view({'post': 'multiple'}), name='planet-multiple'),
    path('planets/search', GamePlanetViewSet.as_view({'post': 'search'}), name='planet-search'),
    path(
        'planet/<str:planet_natural_id>/popr',
        GamePlanetViewSet.as_view({'get': 'latest_popr'}),
        name='planet-infrastructure',
    ),
    path('planet/<str:planet_natural_id>', GamePlanetViewSet.as_view({'get': 'retrieve'}), name='planet-detail'),
    path('planets/<str:search_term>', GamePlanetViewSet.as_view({'get': 'search_single'}), name='planet-search-single'),
    path('planets/', GamePlanetViewSet.as_view({'get': 'list'}), name='planet-list'),
    path('exchanges/', GameExchangeViewSet.as_view({'get': 'list'}), name='exchange-list'),
    path(
        'cxpc/<str:ticker>/<str:exchange_code>',
        ExchangeCXPCViewSet.as_view({'get': 'cxpc_exchange_data'}),
        name='cxpc-market-data-full',
    ),
    path(
        'cxpc/<str:ticker>',
        ExchangeCXPCViewSet.as_view({'get': 'cxpc_ticker_data'}),
        name='cxpc-market-data-ticker',
    ),
    path('storage/', GameStorageViewSet.as_view(actions={'get': 'retrieve'}), name='storage-retrieve'),
]
