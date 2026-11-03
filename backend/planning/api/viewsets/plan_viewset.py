from typing import Any

from core.services.cache_manager import CacheManager
from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from planning.api.serializers import (
    PlanningPlanDetailResponseSerializer,
    PlanningPlanDetailSerializer,
)
from planning.api.serializers.conflict import PlanningSaveConflictSerializer
from planning.models import PlanningEmpire, PlanningPlan
from planning.planning_cache_manager import PLANNING
from planning.services import plan_version_service
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


@extend_schema(tags=['planning : plans'])
class PlanViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    serializer_class = PlanningPlanDetailSerializer

    def get_queryset(self):
        return (
            PlanningPlan.objects.filter(user=self.request.user)
            .prefetch_related(
                Prefetch('empires', queryset=PlanningEmpire.objects.select_related('cx').defer('empire_state'))
            )
            .order_by('plan_name')
        )

    @extend_schema(summary='List all users plans', responses=PlanningPlanDetailResponseSerializer(many=True))
    def list(self, request, *args, **kwargs):
        user_id = request.user.id

        def fetch_data() -> list[dict[str, Any]]:
            return self.get_serializer(self.get_queryset(), many=True).data

        return CacheManager.respond(request, PLANNING, 'plan-list', build=fetch_data, scope=user_id)

    @extend_schema(summary='Get a users specific plan', responses=PlanningPlanDetailResponseSerializer)
    def retrieve(self, request, *args, **kwargs):
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        pk = self.kwargs.get(lookup_url_kwarg)
        user_id = request.user.id

        def fetch_data() -> dict[str, Any]:
            return self.get_serializer(get_object_or_404(self.get_queryset(), pk=pk, user=request.user)).data

        return CacheManager.respond(request, PLANNING, 'plan-retrieve', pk, build=fetch_data, scope=user_id)

    @extend_schema(summary='Creates a new plan', responses={201: PlanningPlanDetailResponseSerializer})
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @extend_schema(
        summary='Updates an existing plan',
        responses={200: PlanningPlanDetailResponseSerializer, 409: PlanningSaveConflictSerializer},
    )
    def update(self, request, *args, **kwargs):
        # UpdateModelMixin.update without its prefetch reset: a save never changes the plan's empires, and the reset
        # re-read them with empire_state and one CX query each
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=kwargs.pop('partial', False))
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data)

    @extend_schema(summary='Delete a specific plan')
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @extend_schema(
        summary='Clone an existing plan', request=None, responses={201: PlanningPlanDetailResponseSerializer}
    )
    @action(detail=True, methods=['post'])
    def clone(self, request, pk=None):
        new_plan = plan_version_service.clone_plan(self.get_object(), request.user)
        return Response(self.get_serializer(new_plan).data, status=status.HTTP_201_CREATED)

    # will also lead to the signals
    def perform_create(self, serializer):
        serializer.save()

    def perform_update(self, serializer):
        serializer.save()

    def perform_destroy(self, instance):
        # holding the row first: a running save could otherwise add a version after the cascade collected them
        with transaction.atomic():
            get_object_or_404(PlanningPlan.objects.select_for_update(), pk=instance.pk).delete()
