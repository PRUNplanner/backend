from typing import cast
from uuid import UUID

from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from planning.api.serializers import (
    PlanningEmpireDetailSerializer,
    PlanningEmpireJunctionsSerializer,
    PlanningEmpirePlanSyncErrorSerializer,
    PlanningEmpirePlanSyncSuccessSerializer,
)
from planning.models import PlanningEmpire, PlanningEmpirePlan, PlanningPlan
from planning.planning_cache_manager import PlanningCacheManager
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from user.models import User


@extend_schema(tags=['planning : empire'])
class EmpireViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]
    serializer_class = PlanningEmpireDetailSerializer

    def get_queryset(self):
        return PlanningEmpire.objects.filter(user=self.request.user).prefetch_related('plans', 'cx').all()

    @extend_schema(summary='List all users empires')
    def list(self, request, *args, **kwargs) -> Response:
        user_id = request.user.id

        def fetch_data():
            empires = self.get_queryset()
            return self.get_serializer(empires, many=True).data

        return PlanningCacheManager.get_empire_list_response(user_id=user_id, func=fetch_data)

    @extend_schema(summary='Get a users specific empire')
    def retrieve(self, request, *args, **kwargs) -> Response:
        pk: UUID = cast(UUID, kwargs.get('pk'))
        user_id = request.user.id

        def fetch_data():
            return self.get_serializer(get_object_or_404(self.get_queryset(), pk=pk)).data

        return PlanningCacheManager.get_empire_retrieve_response(
            user_id=user_id,
            empire_id=pk,
            func=fetch_data,
        )

    @extend_schema(summary='Updates an existing exmpire')
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @extend_schema(summary='Creates a new empire')
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @extend_schema(summary='Delete a specific plan')
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @extend_schema(
        request=PlanningEmpireJunctionsSerializer,
        responses={200: PlanningEmpirePlanSyncSuccessSerializer, 403: PlanningEmpirePlanSyncErrorSerializer},
        summary='Update empire-plan junctions',
    )
    @action(detail=False, methods=['post'], url_path='empire-junctions')
    def sync_junctions(self, request):
        # input validation
        serializer = PlanningEmpireJunctionsSerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)

        user: User = request.user
        payload = serializer.validated_data

        # extract UUIDs
        req_empire_uuids = {item['empire_uuid'] for item in payload}
        req_plan_uuids = {plan['baseplanner_uuid'] for item in payload for plan in item['baseplanners']}

        # fetch user owned empires and plans
        owned_empire_uuids = set(
            PlanningEmpire.objects.filter(user=user, uuid__in=req_empire_uuids).values_list('uuid', flat=True)
        )
        owned_plan_uuids = set(
            PlanningPlan.objects.filter(user=user, uuid__in=req_plan_uuids).values_list('uuid', flat=True)
        )

        unauthorized_empires = req_empire_uuids - owned_empire_uuids
        unauthorized_plans = req_plan_uuids - owned_plan_uuids

        # check, if any requested uuid is NOT in the owned sets
        if unauthorized_empires or unauthorized_plans:
            return Response(
                {
                    'error': 'Unauthorized or non-existend objects referenced',
                    'invalid_empires': list(unauthorized_empires),
                    'invalid_plans': list(unauthorized_plans),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # desired state pairs
        desired_pairs = set()
        for item in payload:
            e_id = item['empire_uuid']
            for plan in item['baseplanners']:
                p_id = plan['baseplanner_uuid']
                desired_pairs.add((e_id, p_id))

        # database sync
        with transaction.atomic():
            # already existing links
            existing_links = PlanningEmpirePlan.objects.filter(user=user).values_list('uuid', 'empire_id', 'plan_id')

            # mapping (empire_id, plan_id) -> junction_uuid
            existing_map = {(e_id, p_id): jct_uuid for jct_uuid, e_id, p_id in existing_links}
            existing_pairs = set(existing_map.keys())

            # diffs
            to_delete_uuids = [existing_map[pair] for pair in (existing_pairs - desired_pairs)]
            to_create_pairs = desired_pairs - existing_pairs

            # execution of parts
            if to_delete_uuids:
                PlanningEmpirePlan.objects.filter(uuid__in=to_delete_uuids).delete()
            if to_create_pairs:
                PlanningEmpirePlan.objects.bulk_create(
                    [PlanningEmpirePlan(user=user, empire_id=e_id, plan_id=p_id) for e_id, p_id in to_create_pairs]
                )

        if to_delete_uuids or to_create_pairs:
            PlanningCacheManager.delete_pattern(f'*PLANNING:{user.id}:*')

        return Response({'status': 'Empire-Plan junctions updated'}, status=status.HTTP_200_OK)

    def perform_update(self, serializer):
        serializer.save()

    def perform_create(self, serializer):
        serializer.save()
