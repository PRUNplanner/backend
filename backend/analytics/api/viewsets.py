from datetime import timedelta

from analytics.api.serializer import AnalyticsPlanAggregateSerializer
from analytics.models import AnalyticsEmpireMaterialSnapshot, AnalyticsPlanAggregate
from analytics.services.analytics_cache_manager import AnalyticsCacheManager
from django.db.models import Sum
from django.http import Http404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from gamedata.models.game_planet import GamePlanet
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound


class AnalyticsPlanAggregateViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AnalyticsPlanAggregate.objects.all()
    lookup_field = 'planet_natural_id'
    serializer_class = AnalyticsPlanAggregateSerializer

    @extend_schema(auth=[], summary='Fetch planet insights by Planet Natural Id')
    def retrieve(self, request, *args, **kwargs):
        planet_id: str = kwargs.get('planet_natural_id', '')

        if not GamePlanet.objects.filter(planet_natural_id=planet_id).exists():
            raise NotFound(detail='Planet not found.')

        def fetch_data(planet_natural_id: str):

            try:
                # try to get aggregate
                instance = self.get_object()
                serializer = self.get_serializer(instance)
                return serializer.data
            except (AnalyticsPlanAggregate.DoesNotExist, Http404):
                # return a 200 OK, but without any data
                return {
                    'status': 'below_threshold',
                    'planet_natural_id': planet_natural_id,
                    'total_plans_analyzed': 0,
                    'aggregated_data': None,
                }

        return AnalyticsCacheManager.get_plan_aggregate_response(planet_id, lambda: fetch_data(planet_id))


class AnalyticsMarketInsightViewSet(viewsets.ViewSet):
    @extend_schema(auth=[], summary='Fetch planning insights for materials')
    @action(detail=False, methods=['get'], url_path='get-global-tracker')
    def get_global_materials(self, request):

        def fetch_data():

            active_cutoff = timezone.now() - timedelta(days=30)

            stats_queryset = (
                AnalyticsEmpireMaterialSnapshot.objects.filter(empire__modified_at__gte=active_cutoff)
                .values('material_ticker')
                .annotate(total_p=Sum('production'), total_c=Sum('consumption'), net_d=Sum('delta'))
                .order_by('material_ticker')
            )

            return list(stats_queryset.values_list('material_ticker', 'total_p', 'total_c', 'net_d'))

        return AnalyticsCacheManager.get_planning_insight_materials(fetch_data)
