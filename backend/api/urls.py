from django.conf import settings
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
    path('client-errors/', views.ClientErrorView.as_view(), name='client-errors'),
    # schema / docs
    path('schema/', SpectacularAPIView.as_view(), name='schema'),
    path('docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    # app endpoints
    path('user/', include('user.api.urls', namespace='user')),
    path('data/', include('gamedata.api.urls', namespace='data')),
    path('planning/', include('planning.api.urls', namespace='planning')),
    path('analytics/', include('analytics.api.urls', namespace='analytics')),
]

# DRF's session login for the browsable API, which only local settings render
if settings.DEBUG:
    urlpatterns.append(path('api-auth/', include('rest_framework.urls')))
