from datetime import timedelta

from analytics.api.serializer import AnalyticsMarketInsightSerializer, AnalyticsPlanAggregateSerializer
from analytics.models import AnalyticsEmpireMaterialSnapshot, AnalyticsPlanAggregate
from analytics.services.analytics_cache_manager import MATERIALS_INSIGHT
from core.services.cache_manager import CacheManager
from django.db.models import Sum
from django.http import Http404
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, extend_schema
from gamedata.models.game_planet import GamePlanet
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response


class AnalyticsPlanAggregateViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AnalyticsPlanAggregate.objects.all()
    lookup_field = 'planet_natural_id'
    serializer_class = AnalyticsPlanAggregateSerializer

    @extend_schema(auth=[], summary='Fetch planet insights by Planet Natural Id')
    def retrieve(self, request, *args, **kwargs):
        planet_natural_id: str = kwargs.get('planet_natural_id', '')

        if not GamePlanet.objects.filter(planet_natural_id=planet_natural_id).exists():
            raise NotFound(detail='Planet not found.')

        try:
            return Response(self.get_serializer(self.get_object()).data)
        except (AnalyticsPlanAggregate.DoesNotExist, Http404):
            # return a 200 OK, but without any data
            return Response(
                {
                    'status': 'below_threshold',
                    'planet_natural_id': planet_natural_id,
                    'total_plans_analyzed': 0,
                    'aggregated_data': None,
                }
            )


class AnalyticsMarketInsightViewSet(viewsets.ViewSet):
    @extend_schema(
        auth=[],
        summary='Fetch planning insights for materials',
        responses={200: AnalyticsMarketInsightSerializer},
        examples=[
            OpenApiExample(
                'Material Insights Example',
                summary='Example for positional array response',
                value=[['AAR', 30.3584, 12.8593, 17.4991], ['ABH', 120.7919, 0.0, 120.7919]],
                response_only=True,
            )
        ],
    )
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

        return CacheManager.respond(request, MATERIALS_INSIGHT, 'global-tracker', build=fetch_data)
