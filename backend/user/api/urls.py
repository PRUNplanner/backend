from django.urls import path
from drf_spectacular.utils import extend_schema
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
)

from .viewsets import (
    CustomTokenRefreshView,
    UserAPIKeyViewSet,
    UserEmailVerificationViewSet,
    UserPasswordResetViewSet,
    UserPreferenceViewSet,
    UserProfileViewSet,
    UserRegisterViewSet,
)

app_name = 'user'


@extend_schema(tags=['user : authentication'], summary='Login and retrieve tokens')
class DecoratedTokenObtainPairView(TokenObtainPairView):
    pass


urlpatterns = [
    path('signup/', UserRegisterViewSet.as_view({'post': 'create'}), name='user_signup'),
    path('login/', DecoratedTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('refresh/', CustomTokenRefreshView.as_view(), name='token_refresh'),
    path('profile/', UserProfileViewSet.as_view({'get': 'retrieve', 'patch': 'update_profile'}), name='user_profile'),
    path('change_password/', UserProfileViewSet.as_view({'post': 'change_password'}), name='user_change_password'),
    path(
        'preferences/', UserPreferenceViewSet.as_view({'get': 'retrieve', 'patch': 'update'}), name='user_preferences'
    ),
    path(
        'api/keys/',
        UserAPIKeyViewSet.as_view(
            {
                'get': 'list',
                'post': 'create',
            }
        ),
        name='user_apikey_list',
    ),
    path('api/keys/<str:pk>/', UserAPIKeyViewSet.as_view({'delete': 'destroy'}), name='user_apikey_detail'),
    path(
        'request_email_verification/',
        UserEmailVerificationViewSet.as_view({'post': 'request_code'}),
        name='user_request_email_verification',
    ),
    path('verify_email/', UserEmailVerificationViewSet.as_view({'post': 'verify_email'}), name='user_verify_email'),
    path(
        'request_password_reset/',
        UserPasswordResetViewSet.as_view({'post': 'request_code'}),
        name='user_request_password_reset',
    ),
    path(
        'password_reset/',
        UserPasswordResetViewSet.as_view({'post': 'password_reset'}),
        name='user_password_reset',
    ),
]
