from typing import Any

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from planning.api.serializers import (
    PlanningPlanDetailSerializer,
)
from planning.models import PlanningPlan
from planning.planning_cache_manager import PlanningCacheManager
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
        return PlanningPlan.objects.filter(user=self.request.user).prefetch_related('empires').all()

    @extend_schema(summary='List all users plans')
    def list(self, request, *args, **kwargs):
        user_id = request.user.id

        def fetch_data() -> list[dict[str, Any]]:
            return self.get_serializer(self.get_queryset(), many=True).data

        return PlanningCacheManager.get_plan_list_response(
            user_id=user_id,
            func=fetch_data,
        )

    @extend_schema(summary='Get a users specific plan')
    def retrieve(self, request, *args, **kwargs):
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        pk = self.kwargs.get(lookup_url_kwarg)
        user_id = request.user.id

        def fetch_data() -> dict[str, Any]:
            return self.get_serializer(get_object_or_404(self.get_queryset(), pk=pk, user=request.user)).data

        return PlanningCacheManager.get_plan_retrieve_response(
            user_id=user_id,
            plan_id=pk,
            func=fetch_data,
        )

    @extend_schema(summary='Creates a new plan')
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @extend_schema(summary='Updates an existing plan')
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @extend_schema(summary='Delete a specific plan')
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @extend_schema(summary='Clone an existing plan', request=None, responses={201: PlanningPlanDetailSerializer})
    @action(detail=True, methods=['post'])
    def clone(self, request, pk=None):
        original_plan = self.get_object()

        new_plan = original_plan
        new_plan.pk = None
        new_plan.uuid = None

        new_plan.plan_name = f'{original_plan.plan_name} (Clone)'
        new_plan.save()

        serializer = self.get_serializer(new_plan)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    # will also lead to the signals
    def perform_create(self, serializer):
        serializer.save()

    def perform_update(self, serializer):
        serializer.save()
