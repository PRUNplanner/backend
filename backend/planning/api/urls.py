from django.urls import path

from .viewsets import CXViewSet, EmpireViewSet, PlanViewSet, SharedViewSet

app_name = 'planning'
urlpatterns = [
    path('plan/', PlanViewSet.as_view({'get': 'list', 'post': 'create'}), name='plan'),
    path(
        'plan/<uuid:pk>',
        PlanViewSet.as_view({'get': 'retrieve', 'put': 'update', 'delete': 'destroy'}),
        name='plan-detail',
    ),
    path(
        'plan/<uuid:pk>/clone/',
        PlanViewSet.as_view({'post': 'clone'}),
        name='plan-clone',
    ),
    path('empire/', EmpireViewSet.as_view({'get': 'list', 'post': 'create'}), name='empire'),
    path('empire/junctions/', EmpireViewSet.as_view({'post': 'sync_junctions'}), name='empire-junctions'),
    path(
        'empire/<uuid:pk>',
        EmpireViewSet.as_view({'get': 'retrieve', 'put': 'update', 'delete': 'destroy'}),
        name='empire-detail',
    ),
    path(
        'empire/<uuid:pk>/plans',
        EmpireViewSet.as_view({'get': 'retrieve_plans'}),
        name='empire-plan-list',
    ),
    path('cx/', CXViewSet.as_view({'get': 'list', 'post': 'create'}), name='cx'),
    path('cx/junctions/', CXViewSet.as_view({'post': 'sync_junctions'}), name='cx-junctions'),
    path(
        'cx/<uuid:pk>',
        CXViewSet.as_view({'get': 'retrieve', 'put': 'update', 'delete': 'destroy'}),
        name='cx-detail',
    ),
    path('shared/', SharedViewSet.as_view({'get': 'list', 'post': 'create'}), name='shared'),
    path('shared/<uuid:pk>', SharedViewSet.as_view({'get': 'retrieve', 'delete': 'destroy'}), name='shared-detail'),
    path('shared/<uuid:pk>/clone', SharedViewSet.as_view({'post': 'clone'}), name='shared-clone'),
]
