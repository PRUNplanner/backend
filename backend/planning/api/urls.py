from django.urls import path
from rest_framework_csv.renderers import CSVRenderer

from .viewsets import CXViewSet, EmpireViewSet, PlanViewSet, SharedViewSet
from .viewsets.plan_version_viewset import PlanDraftViewSet, PlanVersionViewSet

app_name = 'planning'
urlpatterns = [
    path('plan/', PlanViewSet.as_view({'get': 'list', 'post': 'create'}), name='plan'),
    path(
        'plan/<uuid:pk>/',
        PlanViewSet.as_view({'get': 'retrieve', 'put': 'update', 'delete': 'destroy'}),
        name='plan-detail',
    ),
    path(
        'plan/<uuid:pk>/clone/',
        PlanViewSet.as_view({'post': 'clone'}),
        name='plan-clone',
    ),
    path('plan/<uuid:plan_pk>/versions/', PlanVersionViewSet.as_view({'get': 'list'}), name='plan-versions'),
    path(
        'plan/<uuid:plan_pk>/versions/<uuid:pk>/',
        PlanVersionViewSet.as_view({'get': 'retrieve', 'patch': 'partial_update', 'delete': 'destroy'}),
        name='plan-version-detail',
    ),
    path(
        'plan/<uuid:plan_pk>/versions/<uuid:pk>/clone/',
        PlanVersionViewSet.as_view({'post': 'clone'}),
        name='plan-version-clone',
    ),
    path(
        'plan/<uuid:plan_pk>/versions/<uuid:pk>/activate/',
        PlanVersionViewSet.as_view({'post': 'activate'}),
        name='plan-version-activate',
    ),
    path(
        'plan/<uuid:plan_pk>/draft/',
        PlanDraftViewSet.as_view({'get': 'retrieve', 'put': 'update', 'delete': 'destroy'}),
        name='plan-draft',
    ),
    path('empire/', EmpireViewSet.as_view({'get': 'list', 'post': 'create'}), name='empire'),
    path('empire/junctions/', EmpireViewSet.as_view({'post': 'sync_junctions'}), name='empire-junctions'),
    path(
        'empire/<uuid:pk>/',
        EmpireViewSet.as_view({'get': 'retrieve', 'put': 'update', 'delete': 'destroy'}),
        name='empire-detail',
    ),
    path(
        'empire/<uuid:pk>/plans/',
        EmpireViewSet.as_view({'get': 'retrieve_plans'}),
        name='empire-plan-list',
    ),
    path(
        'empire/<uuid:pk>/plans/csv/',
        EmpireViewSet.as_view({'get': 'retrieve_plans_csv'}, renderer_classes=[CSVRenderer]),
        name='empire-plan-list-csv',
    ),
    path(
        'empire/<uuid:pk>/plans/material_io/',
        EmpireViewSet.as_view({'get': 'plans_material_io'}),
        name='empire-plans-material-io',
    ),
    path(
        'empire/<uuid:pk>/plans/material_io/csv/',
        EmpireViewSet.as_view({'get': 'plans_material_io_csv'}, renderer_classes=[CSVRenderer]),
        name='empire-plans-material-io-csv',
    ),
    path(
        'empire/<uuid:pk>/material_io/',
        EmpireViewSet.as_view({'get': 'material_io'}),
        name='empire-material-io',
    ),
    path(
        'empire/<uuid:pk>/material_io/csv/',
        EmpireViewSet.as_view({'get': 'material_io_csv'}, renderer_classes=[CSVRenderer]),
        name='empire-material-io-csv',
    ),
    path(
        'empire/<uuid:pk>/state/',
        EmpireViewSet.as_view({'patch': 'sync_state'}),
        name='empire-sync-state',
    ),
    path('cx/', CXViewSet.as_view({'get': 'list', 'post': 'create'}), name='cx'),
    path('cx/junctions/', CXViewSet.as_view({'post': 'sync_junctions'}), name='cx-junctions'),
    path(
        'cx/<uuid:pk>/',
        CXViewSet.as_view({'get': 'retrieve', 'put': 'update', 'delete': 'destroy'}),
        name='cx-detail',
    ),
    path('shared/', SharedViewSet.as_view({'get': 'list', 'post': 'create'}), name='shared'),
    path('shared/<uuid:pk>/', SharedViewSet.as_view({'get': 'retrieve', 'delete': 'destroy'}), name='shared-detail'),
    path('shared/<uuid:pk>/clone/', SharedViewSet.as_view({'post': 'clone'}), name='shared-clone'),
]
