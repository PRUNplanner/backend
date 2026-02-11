from typing import cast
from uuid import UUID

from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from planning.api.serializers import (
    PlanningCXDetailSerializer,
    PlanningCXJunctionsSyncErrorSerializer,
    PlanningCXJunctionsSyncSuccessSerializer,
    PlanningCXJunctionUpdateSerializer,
)
from planning.models import PlanningCX, PlanningEmpire
from planning.planning_cache_manager import PlanningCacheManager
from rest_framework import mixins, status, viewsets
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
        return PlanningCX.objects.filter(user=self.request.user).all()

    @extend_schema(summary='List all cx preferences')
    def list(self, request, *args, **kwargs) -> Response:
        user_id = request.user.id

        def fetch_data():
            return self.get_serializer(self.get_queryset(), many=True).data

        return PlanningCacheManager.get_cx_list_response(user_id=user_id, func=fetch_data)

    @extend_schema(summary='Get a users specific cx preference')
    def retrieve(self, request, *args, **kwargs):
        pk: UUID = cast(UUID, kwargs.get('pk'))
        user_id = request.user.id

        def fetch_data():
            return self.get_serializer(get_object_or_404(self.get_queryset(), pk=pk)).data

        return PlanningCacheManager.get_cx_retrieve_response(user_id=user_id, cx_id=pk, func=fetch_data)

    @extend_schema(summary='Creates a new cx preference')
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @extend_schema(summary='Updates an existing cx preference')
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @extend_schema(summary='Delete a specific cx preference')
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @extend_schema(
        request=PlanningCXJunctionUpdateSerializer,
        responses={
            200: PlanningCXJunctionsSyncSuccessSerializer,
            400: PlanningCXJunctionsSyncErrorSerializer,
            403: PlanningCXJunctionsSyncErrorSerializer,
        },
        summary='Update cx-empire junctions',
    )
    @action(detail=False, methods=['post'], url_path='cx-junctions')
    def sync_junctions(self, request):
        serializer = PlanningCXJunctionUpdateSerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)

        user: User = request.user
        payload = serializer.validated_data

        # collect all cx uuids
        input_cx_uuids = {item['cx_uuid'] for item in payload}
        input_empire_uuids = {emp['empire_uuid'] for item in payload for emp in item['empires']}

        # validate ownerships
        ## cx ownership
        if PlanningCX.objects.filter(user=user, uuid__in=input_cx_uuids).count() != len(input_cx_uuids):
            return Response({'error': 'Invalid CX UUIDs detected.'}, status=status.HTTP_403_FORBIDDEN)
        ## empire ownership
        if PlanningEmpire.objects.filter(user=user, uuid__in=input_empire_uuids).count() != len(input_empire_uuids):
            return Response({'error': 'Invalid Empire UUIDs detected.'}, status=status.HTTP_403_FORBIDDEN)

        # no empire is assigned twice
        all_emp_list = [emp['empire_uuid'] for item in payload for emp in item['empires']]
        if len(all_emp_list) != len(set(all_emp_list)):
            return Response({'error': 'Duplicate empire assignment in request.'}, status=status.HTTP_400_BAD_REQUEST)

        # apply changes
        with transaction.atomic():
            # set all cx assignments to None
            PlanningEmpire.objects.filter(user=user).update(cx=None)

            # bulk assign for the empires in the payload
            for item in payload:
                cx_id = item['cx_uuid']
                emp_ids = [e['empire_uuid'] for e in item['empires']]

                if emp_ids:
                    PlanningEmpire.objects.filter(uuid__in=emp_ids).update(cx_id=cx_id)

        return Response({'status': 'CX-Empire junctions updated'})
