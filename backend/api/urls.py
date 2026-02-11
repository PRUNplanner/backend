from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from . import views

urlpatterns = [
    # Index
    path('', views.index, name='index'),
    # UI auth
    path('api-auth/', include('rest_framework.urls')),
    # schema / docs
    path('schema/', SpectacularAPIView.as_view(), name='schema'),
    path('docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    # app endpoints
    path('user/', include('user.api.urls', namespace='user')),
    path('data/', include('gamedata.api.urls', namespace='data')),
    path('planning/', include('planning.api.urls', namespace='planning')),
]
