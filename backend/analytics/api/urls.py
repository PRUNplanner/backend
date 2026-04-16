from analytics.api.viewsets import AnalyticsMarketInsightViewSet, AnalyticsPlanAggregateViewSet
from django.urls import path

app_name = 'analytics'
urlpatterns = [
    path(
        'planet_insights/<str:planet_natural_id>/',
        AnalyticsPlanAggregateViewSet.as_view({'get': 'retrieve'}),
        name='planet-insight-detail',
    ),
    path(
        'planning_insights/materials/',
        AnalyticsMarketInsightViewSet.as_view({'get': 'get_global_materials'}),
        name='planning-insight-materials',
    ),
]
