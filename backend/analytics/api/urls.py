from analytics.api.viewsets import AnalyticsPlanAggregateViewSet
from django.urls import path

app_name = 'analytics'
urlpatterns = [
    path(
        'planet_insights/<str:planet_natural_id>/',
        AnalyticsPlanAggregateViewSet.as_view({'get': 'retrieve'}),
        name='planet-insight-detail',
    ),
]
