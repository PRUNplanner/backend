from analytics.api.serializer import AnalyticsPlanAggregateSerializer
from analytics.models import AnalyticsPlanAggregate
from django.http import Http404
from drf_spectacular.utils import extend_schema
from gamedata.models.game_planet import GamePlanet
from rest_framework import status, viewsets
from rest_framework.exceptions import NotFound
from rest_framework.response import Response


class AnalyticsPlanAggregateViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AnalyticsPlanAggregate.objects.all()
    lookup_field = 'planet_natural_id'
    serializer_class = AnalyticsPlanAggregateSerializer

    @extend_schema(auth=[], summary='Fetch planning insights for planet')
    def retrieve(self, request, *args, **kwargs):
        planet_id = kwargs.get('planet_natural_id')

        if not GamePlanet.objects.filter(planet_natural_id=planet_id).exists():
            return Response({'detail': 'Planet not found.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            # try to get aggregate
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            return Response(serializer.data)
        except (AnalyticsPlanAggregate.DoesNotExist, Http404, NotFound):
            # return a 200 OK, but without any data
            return Response(
                {
                    'status': 'below_threshold',
                    'planet_natural_id': planet_id,
                    'total_plans_analyzed': 0,
                    'aggregated_data': None,
                },
                status=status.HTTP_200_OK,
            )
