from typing import cast
from uuid import UUID

from core.services.cache_manager import CacheManager
from django.db.models import Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from planning.api.serializers import (
    PlanningCXDetailSerializer,
    PlanningCXJunctionsSyncErrorSerializer,
    PlanningCXJunctionUpdateSerializer,
)
from planning.api.serializers.conflict import PlanningSaveConflictSerializer
from planning.api.serializers.minimal import MINIMAL_PLAN_FIELDS
from planning.models import PlanningCX, PlanningEmpire, PlanningPlan
from planning.planning_cache_manager import PLANNING
from planning.services.junction_service import JunctionRejected, sync_cx_empires
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from user.models import User


@extend_schema(tags=['planning : cx'])
class CXViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    serializer_class = PlanningCXDetailSerializer

    def get_queryset(self):
        plans = PlanningPlan.objects.filter(user=self.request.user).only(*MINIMAL_PLAN_FIELDS)
        empires = PlanningEmpire.objects.defer('empire_state').prefetch_related(Prefetch('plans', queryset=plans))
        return (
            PlanningCX.objects.filter(user=self.request.user)
            .prefetch_related(Prefetch('cxs', queryset=empires))
            .order_by('cx_name')
        )

    @extend_schema(summary='List all cx preferences')
    def list(self, request, *args, **kwargs) -> HttpResponse:
        user_id = request.user.id

        def fetch_data():
            return self.get_serializer(self.get_queryset(), many=True).data

        return CacheManager.respond(request, PLANNING, 'cx-list', build=fetch_data, scope=user_id)

    @extend_schema(summary='Get a users specific cx preference')
    def retrieve(self, request, *args, **kwargs):
        pk: UUID = cast(UUID, kwargs.get('pk'))
        user_id = request.user.id

        def fetch_data():
            return self.get_serializer(get_object_or_404(self.get_queryset(), pk=pk)).data

        return CacheManager.respond(request, PLANNING, 'cx-retrieve', pk, build=fetch_data, scope=user_id)

    @extend_schema(summary='Creates a new cx preference')
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @extend_schema(
        summary='Updates an existing cx preference',
        responses={200: PlanningCXDetailSerializer, 409: PlanningSaveConflictSerializer},
    )
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @extend_schema(summary='Delete a specific cx preference')
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @extend_schema(
        request=PlanningCXJunctionUpdateSerializer(many=True),
        responses={
            200: PlanningCXDetailSerializer(many=True),
            400: PlanningCXJunctionsSyncErrorSerializer,
            403: PlanningCXJunctionsSyncErrorSerializer,
        },
        summary='Update cx-empire junctions',
    )
    @action(detail=False, methods=['post'])
    def sync_junctions(self, request):
        serializer = PlanningCXJunctionUpdateSerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)

        assignments = [
            (item['cx_uuid'], [e['empire_uuid'] for e in item['empires']]) for item in serializer.validated_data
        ]
        try:
            sync_cx_empires(cast(User, request.user), assignments)
        except JunctionRejected as exc:
            return Response(exc.body, status=exc.status_code)

        return self.list(request)
