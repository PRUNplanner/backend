from django.db import transaction
from django.db.models import F
from drf_spectacular.utils import extend_schema
from planning.api.serializers import (
    PlanningPlanDetailSerializer,
    PlanningSharedCreateSerializer,
    PlanningSharedDetailSerializer,
    PlanningSharedSerializer,
)
from planning.models import PlanningPlan, PlanningShared
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response


@extend_schema(tags=['planning : shared'])
class SharedViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = PlanningSharedSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'uuid'
    lookup_url_kwarg = 'pk'

    def get_queryset(self):
        if self.action == 'retrieve' or self.action == 'clone':
            return PlanningShared.objects.all()
        return PlanningShared.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return PlanningSharedDetailSerializer
        if self.action == 'create':
            return PlanningSharedCreateSerializer
        return PlanningSharedSerializer

    def get_permissions(self):
        if self.action == 'retrieve':
            return [AllowAny()]
        return [IsAuthenticated()]

    @extend_schema(summary='List all shared plans of the user')
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(summary='Delete plan share')
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @extend_schema(auth=[], summary='Get a shared plans information')
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()

        PlanningShared.objects.filter(pk=instance.pk).update(view_count=F('view_count') + 1)

        instance.refresh_from_db()

        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @extend_schema(auth=[], summary='Share a plan')
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        plan = serializer.validated_data['plan']

        # get existing or create new one
        instance, created = PlanningShared.objects.get_or_create(plan=plan, user=request.user)

        # serialize with created uuid
        full_serializer = PlanningSharedDetailSerializer(instance)
        headers = self.get_success_headers(full_serializer.data)
        return Response(
            full_serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK, headers=headers
        )

    @extend_schema(summary='Clone a shared plan')
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    def clone(self, request, pk=None):
        shared_instance = self.get_object()
        original_plan = shared_instance.plan

        with transaction.atomic():
            cloned_plan = PlanningPlan.objects.create(
                user=request.user,
                plan_name=f'{original_plan.planet_natural_id} (Shared Clone)',
                planet_natural_id=original_plan.planet_natural_id,
                plan_permits_used=original_plan.plan_permits_used,
                plan_cogc=original_plan.plan_cogc,
                plan_corphq=original_plan.plan_corphq,
                plan_data=original_plan.plan_data,
                schema_version=original_plan.schema_version,
            )

        return Response(PlanningPlanDetailSerializer(cloned_plan).data, status=status.HTTP_201_CREATED)
