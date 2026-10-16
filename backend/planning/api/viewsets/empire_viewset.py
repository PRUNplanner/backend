from collections.abc import Callable
from typing import cast
from uuid import UUID

from core.services.cache_manager import CacheManager
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, extend_schema
from planning.api.serializers import (
    PlanningEmpireDetailSerializer,
    PlanningEmpireJunctionsSerializer,
    PlanningEmpirePlanSyncErrorSerializer,
    PlanningPlanListSerializer,
)
from planning.api.serializers.conflict import PlanningSaveConflictSerializer
from planning.api.serializers.empire import (
    PlanningEmpireMaterialIOExportSerializer,
    PlanningEmpirePlanMaterialIOExportSerializer,
    PlanningEmpireStateUpdateSerializer,
)
from planning.models import PlanningEmpire, PlanningEmpirePlan, PlanningPlan
from planning.planning_cache_manager import PLANNING
from planning.services.empire_export_service import (
    MATERIAL_IO_HEADER,
    PLAN_LIST_HEADER,
    PLAN_MATERIAL_IO_HEADER,
    EmpireExportService,
    Row,
)
from planning.services.empire_state_service import EmpireStateService
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from user.models import User


def _state_export(
    request: HttpRequest,
    pk: UUID,
    endpoint: str,
    rows: Callable[[PlanningEmpire], list[Row]],
    csv_header: list[str] | None = None,
) -> HttpResponse:
    # modified_at moves on every state sync (which doesn't invalidate PLANNING), so it goes into the key;
    # empire_state is deferred and only loads on a cache miss
    empire = get_object_or_404(PlanningEmpire.objects.filter(user=request.user).only('uuid', 'modified_at'), pk=pk)

    def fetch_data():
        data = rows(empire)
        return data if csv_header else {'updated_at': EmpireExportService.updated_at(empire), 'rows': data}

    return CacheManager.respond(
        request,
        PLANNING,
        endpoint,
        pk,
        empire.modified_at.isoformat(),
        build=fetch_data,
        scope=request.user.pk,
        fmt='csv' if csv_header else 'json',
        csv_header=csv_header,
    )


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
        return (
            PlanningEmpire.objects.filter(user=self.request.user)
            .defer('empire_state')
            .prefetch_related('plans', 'cx')
            .order_by('empire_name')
        )

    def get_serializer_class(self):
        if self.action == 'sync_state':
            return PlanningEmpireStateUpdateSerializer
        return super().get_serializer_class()

    @extend_schema(summary='List all users empires')
    def list(self, request, *args, **kwargs) -> HttpResponse:
        user_id = request.user.id

        def fetch_data():
            empires = self.get_queryset()
            return self.get_serializer(empires, many=True).data

        return CacheManager.respond(request, PLANNING, 'empire-list', build=fetch_data, scope=user_id)

    @extend_schema(summary='Get a users specific empire')
    def retrieve(self, request, *args, **kwargs) -> HttpResponse:
        pk: UUID = cast(UUID, kwargs.get('pk'))
        user_id = request.user.id

        def fetch_data():
            return self.get_serializer(get_object_or_404(self.get_queryset(), pk=pk)).data

        return CacheManager.respond(request, PLANNING, 'empire-retrieve', pk, build=fetch_data, scope=user_id)

    @extend_schema(summary='Get all plans of the users specific empire')
    def retrieve_plans(self, request, *args, **kwargs) -> HttpResponse:
        pk: UUID = cast(UUID, kwargs.get('pk'))
        user_id = request.user.id

        def fetch_data():
            return PlanningPlanListSerializer(get_object_or_404(self.get_queryset(), pk=pk).plans.all(), many=True).data

        return CacheManager.respond(request, PLANNING, 'empire-plans', pk, build=fetch_data, scope=user_id)

    @extend_schema(
        tags=['planning : csv'],
        summary='Get all plans of the users specific empire (CSV)',
        responses={(200, 'text/csv'): OpenApiTypes.STR},
        examples=[
            OpenApiExample(
                'CSV Example',
                value=(
                    'plan_uuid,plan_name,planet_natural_id,plan_cogc,plan_permits_used,plan_corphq,modified_at\n'
                    '0b4f3c1e-8a52-4d1b-9c55-1f0e4d2a7b10,Montem RAT,OT-580b,FOOD_INDUSTRIES,1,False,'
                    '2026-10-16T09:12:44.123456Z\n'
                ),
                media_type='text/csv',
            )
        ],
    )
    def retrieve_plans_csv(self, request, *args, **kwargs) -> HttpResponse:
        pk: UUID = cast(UUID, kwargs.get('pk'))

        def fetch_data():
            plans = get_object_or_404(self.get_queryset(), pk=pk).plans.all()
            return EmpireExportService.plan_list_rows(PlanningPlanListSerializer(plans, many=True).data)

        return CacheManager.respond(
            request,
            PLANNING,
            'empire-plans',
            pk,
            build=fetch_data,
            scope=request.user.id,
            fmt='csv',
            csv_header=PLAN_LIST_HEADER,
        )

    @extend_schema(
        summary='Get the empire material i/o',
        description=(
            'Production, consumption and delta per material and day, from the empire state the frontend saved '
            'when it last calculated the empire (`updated_at`). An empire without a saved state has no rows.'
        ),
        responses={200: PlanningEmpireMaterialIOExportSerializer},
    )
    def material_io(self, request, *args, **kwargs) -> HttpResponse:
        return _state_export(request, kwargs['pk'], 'empire-material-io', EmpireExportService.material_io_rows)

    @extend_schema(
        tags=['planning : csv'],
        summary='Get the empire material i/o (CSV)',
        description=(
            'Production, consumption and delta per material and day, from the empire state the frontend saved '
            'when it last calculated the empire (`updated_at`). Header only for an empire without a saved state.'
        ),
        responses={(200, 'text/csv'): OpenApiTypes.STR},
        examples=[
            OpenApiExample(
                'CSV Example',
                value=(
                    'ticker,material_name,production,consumption,delta,updated_at\n'
                    'DW,drinkingWater,120.0,95.5,24.5,2026-10-16T09:12:44.123Z\n'
                    'RAT,rations,80.0,100.0,-20.0,2026-10-16T09:12:44.123Z\n'
                ),
                media_type='text/csv',
            )
        ],
    )
    def material_io_csv(self, request, *args, **kwargs) -> HttpResponse:
        return _state_export(
            request,
            kwargs['pk'],
            'empire-material-io',
            EmpireExportService.material_io_rows,
            csv_header=MATERIAL_IO_HEADER,
        )

    @extend_schema(
        summary='Get the material i/o of each plan in the empire',
        description=(
            'Production, consumption and delta per plan, material and day, from the empire state the frontend saved '
            'when it last calculated the empire (`updated_at`). Only plans linked to the empire; a newly linked plan '
            'has rows after the next state save.'
        ),
        responses={200: PlanningEmpirePlanMaterialIOExportSerializer},
    )
    def plans_material_io(self, request, *args, **kwargs) -> HttpResponse:
        return _state_export(
            request, kwargs['pk'], 'empire-plans-material-io', EmpireExportService.plan_material_io_rows
        )

    @extend_schema(
        tags=['planning : csv'],
        summary='Get the material i/o of each plan in the empire (CSV)',
        description=(
            'Production, consumption and delta per plan, material and day, from the empire state the frontend saved '
            'when it last calculated the empire (`updated_at`). Only plans linked to the empire.'
        ),
        responses={(200, 'text/csv'): OpenApiTypes.STR},
        examples=[
            OpenApiExample(
                'CSV Example',
                value=(
                    'plan_uuid,plan_name,planet_natural_id,cogc,ticker,production,consumption,delta,updated_at\n'
                    '0b4f3c1e-8a52-4d1b-9c55-1f0e4d2a7b10,Montem RAT,OT-580b,FOOD_INDUSTRIES,RAT,80.0,0.0,80.0,'
                    '2026-10-16T09:12:44.123Z\n'
                ),
                media_type='text/csv',
            )
        ],
    )
    def plans_material_io_csv(self, request, *args, **kwargs) -> HttpResponse:
        return _state_export(
            request,
            kwargs['pk'],
            'empire-plans-material-io',
            EmpireExportService.plan_material_io_rows,
            csv_header=PLAN_MATERIAL_IO_HEADER,
        )

    @extend_schema(
        summary='Updates an existing exmpire',
        responses={200: PlanningEmpireDetailSerializer, 409: PlanningSaveConflictSerializer},
    )
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @extend_schema(summary='Creates a new empire')
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @extend_schema(summary='Delete a specific plan')
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @extend_schema(
        request=PlanningEmpireJunctionsSerializer(many=True),
        responses={200: PlanningEmpireDetailSerializer(many=True), 403: PlanningEmpirePlanSyncErrorSerializer},
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
        desired_pairs = {
            (item['empire_uuid'], plan['baseplanner_uuid']) for item in payload for plan in item['baseplanners']
        }

        # database sync
        with transaction.atomic():
            # already existing links
            existing_links = PlanningEmpirePlan.objects.filter(user=user, empire_id__in=req_empire_uuids).values_list(
                'uuid', 'empire_id', 'plan_id'
            )

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
            CacheManager.invalidate(PLANNING, user.id)

        return self.list(request)

    @extend_schema(
        summary='Sync empire state (empire material i/o, plan material i/o and metadata)',
        request=PlanningEmpireStateUpdateSerializer,
        responses={200: PlanningEmpireDetailSerializer},
    )
    @action(detail=True, methods=['patch'], url_path='empire-sync-state')
    def sync_state(self, request, pk=None):

        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)

        # handle empire + relational snapshot refresh
        EmpireStateService.update_state(instance, serializer.validated_data)

        return Response(PlanningEmpireDetailSerializer(instance).data)

    def perform_update(self, serializer):
        serializer.save()

    def perform_create(self, serializer):
        serializer.save()
