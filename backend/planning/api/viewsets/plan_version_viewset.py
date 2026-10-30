from datetime import datetime
from uuid import UUID

from django.db import transaction
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from planning.api.serializers import PlanningPlanDetailSerializer
from planning.api.serializers.conflict import DraftConflict, PlanningDraftConflictSerializer
from planning.api.serializers.version import (
    PlanningPlanDraftDeleteSerializer,
    PlanningPlanDraftResponseSerializer,
    PlanningPlanDraftSavedSerializer,
    PlanningPlanDraftSerializer,
    PlanningPlanVersionDetailSerializer,
    PlanningPlanVersionRenameSerializer,
    PlanningPlanVersionSerializer,
)
from planning.models import PlanningPlan, PlanningPlanVersion
from planning.services import plan_version_service
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response


def _owned_plan(request: Request, plan_pk: UUID, *, lock: bool = False) -> PlanningPlan:
    # another user's plan is a 404; lock when the view goes on to write
    plans = PlanningPlan.objects.defer('plan_data')
    if lock:
        plans = plans.select_for_update()
    return get_object_or_404(plans, pk=plan_pk, user=request.user)


def _refuse_unseen_draft(plan: PlanningPlan, seen: datetime | None) -> None:
    newer = plan_version_service.newer_draft(plan, seen)
    if newer is not None:
        raise DraftConflict(newer)


def _saved_versions(plan: PlanningPlan) -> QuerySet[PlanningPlanVersion]:
    return PlanningPlanVersion.objects.filter(plan=plan).order_by('-created_at')


def _active_uuid(plan: PlanningPlan) -> UUID | None:
    return _saved_versions(plan).values_list('pk', flat=True).first()


@extend_schema(tags=['planning : plans'])
class PlanVersionViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = PlanningPlanVersionSerializer

    @extend_schema(
        summary='List the saved versions of a plan', responses={200: PlanningPlanVersionSerializer(many=True)}
    )
    def list(self, request: Request, plan_pk: UUID) -> Response:
        plan = _owned_plan(request, plan_pk)
        versions = list(_saved_versions(plan).defer('plan_data'))
        context = {'active_uuid': versions[0].pk if versions else None}
        return Response(PlanningPlanVersionSerializer(versions, many=True, context=context).data)

    @extend_schema(summary='Get a version with its plan data', responses={200: PlanningPlanVersionDetailSerializer})
    def retrieve(self, request: Request, plan_pk: UUID, pk: UUID) -> Response:
        plan = _owned_plan(request, plan_pk)
        version = get_object_or_404(_saved_versions(plan), pk=pk)
        context = {'active_uuid': _active_uuid(plan)}
        return Response(PlanningPlanVersionDetailSerializer(version, context=context).data)

    @extend_schema(
        summary='Name or unname a version',
        request=PlanningPlanVersionRenameSerializer,
        responses={200: PlanningPlanVersionSerializer},
    )
    @transaction.atomic
    def partial_update(self, request: Request, plan_pk: UUID, pk: UUID) -> Response:
        # the lock: a concurrent save may prune this version
        plan = _owned_plan(request, plan_pk, lock=True)
        version = get_object_or_404(_saved_versions(plan).defer('plan_data'), pk=pk)
        serializer = PlanningPlanVersionRenameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        version = plan_version_service.rename_version(version, serializer.validated_data['name'])
        return Response(PlanningPlanVersionSerializer(version, context={'active_uuid': _active_uuid(plan)}).data)

    @extend_schema(summary='Open a version as a new plan', request=None, responses={201: PlanningPlanDetailSerializer})
    def clone(self, request: Request, plan_pk: UUID, pk: UUID) -> Response:
        version = get_object_or_404(_saved_versions(_owned_plan(request, plan_pk)), pk=pk)
        plan = plan_version_service.clone_version(version, request.user)
        return Response(PlanningPlanDetailSerializer(plan).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=['planning : plans'])
class PlanDraftViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = PlanningPlanDraftSerializer

    @property
    def throttle_scope(self) -> str | None:
        return 'plan_draft' if self.action == 'update' else None

    @extend_schema(summary="Get the plan's autosave draft", responses={200: PlanningPlanDraftResponseSerializer})
    def retrieve(self, request: Request, plan_pk: UUID) -> Response:
        draft = PlanningPlanVersion.drafts.filter(plan=_owned_plan(request, plan_pk)).first()
        return Response(PlanningPlanDraftResponseSerializer({'draft': draft}).data)

    @extend_schema(
        summary="Create or replace the plan's autosave draft",
        request=PlanningPlanDraftSerializer,
        responses={200: PlanningPlanDraftSavedSerializer, 409: PlanningDraftConflictSerializer},
    )
    @transaction.atomic
    def update(self, request: Request, plan_pk: UUID) -> Response:
        plan = _owned_plan(request, plan_pk, lock=True)
        serializer = PlanningPlanDraftSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        fields = dict(serializer.validated_data)
        base_modified_at = fields.pop('base_modified_at')
        _refuse_unseen_draft(plan, fields.pop('draft_updated_at'))
        draft = plan_version_service.save_draft(plan, fields, base_modified_at)
        return Response(PlanningPlanDraftSavedSerializer(draft).data)

    @extend_schema(
        summary="Delete the plan's autosave draft",
        parameters=[PlanningPlanDraftDeleteSerializer],
        responses={204: None, 409: PlanningDraftConflictSerializer},
    )
    @transaction.atomic
    def destroy(self, request: Request, plan_pk: UUID) -> Response:
        plan = _owned_plan(request, plan_pk, lock=True)
        query = PlanningPlanDraftDeleteSerializer(data=request.query_params)
        query.is_valid(raise_exception=True)

        _refuse_unseen_draft(plan, query.validated_data['draft_updated_at'])
        plan_version_service.delete_draft(plan)
        return Response(status=status.HTTP_204_NO_CONTENT)
