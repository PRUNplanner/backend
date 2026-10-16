import structlog
from django.core.cache import cache
from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView
from user.api.serializer import (
    PLANET_CHECKLIST,
    CodeGenericResponseSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    UserAPIKeyCreateSerializer,
    UserAPIKeySerializer,
    UserChangePasswordSerializer,
    UserPreferenceSerializer,
    UserProfileSerializer,
    UserRegisterSerializer,
    VerifyCodeSerializer,
)
from user.models import User, UserAPIKey, UserPreference
from user.services import VerificationeCodeChoices, VerificationService
from user.services.verification_service import CODE_DETAILS
from user.tasks import user_handle_post_refresh

logger = structlog.get_logger(__name__)

# a client can refresh its token in bursts; last_login and the FIO check need it once per interval
POST_REFRESH_INTERVAL_SECONDS = 15 * 60

# planOverrides by plan uuid, fioRepairPlanLinks by FIO SiteId
KEYED_PREFERENCES = ('plan_overrides', 'fio_repair_plan_links')


def _coded(code: str, status_code: int) -> Response:
    return Response({'detail': CODE_DETAILS[code], 'code': code}, status=status_code)


def _merge_keyed(stored: dict[str, dict] | None, changes: dict[str, dict | None]) -> dict[str, dict]:
    merged = dict(stored or {})
    for key, value in changes.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


@extend_schema(tags=['user : profile'])
class UserPreferenceViewSet(mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    serializer_class = UserPreferenceSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        obj, created = UserPreference.objects.get_or_create(user=self.request.user)
        return obj.preferences

    @extend_schema(summary='Get preferences')
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @extend_schema(summary='Update preferences')
    def partial_update(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        # top-level merge: omitted keys keep their stored value; keyed preferences merge per key, null deletes one
        changes = dict(serializer.validated_data)
        keyed = {key: changes.pop(key) for key in KEYED_PREFERENCES if key in changes}

        # locked, so another tab's PATCH in between isn't lost
        with transaction.atomic():
            obj, _ = UserPreference.objects.select_for_update().get_or_create(user=request.user)
            preferences = {**obj.preferences, **changes}
            for key, key_changes in keyed.items():
                preferences[key] = _merge_keyed(obj.preferences.get(key), key_changes)
            obj.preferences = preferences
            obj.save()

        return Response(self.get_serializer(obj.preferences).data)


@extend_schema(tags=['user : authentication'])
class UserRegisterViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = UserRegisterSerializer
    permission_classes = [AllowAny]
    throttle_scope = 'auth_register'

    def perform_create(self, serializer):
        serializer.save()

    @extend_schema(auth=[], summary='Register account')
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            # codes only: never log the submitted username, password, email or planet name
            codes = sorted(f'{field}:{error.code}' for field, errors in serializer.errors.items() for error in errors)
            planet = {}
            if 'planet_input:planet_mismatch' in codes and request.data.get('planet_id') in PLANET_CHECKLIST:
                planet['planet_id'] = request.data['planet_id']
            logger.info('user_signup_rejected', fields=sorted(serializer.errors), codes=codes, **planet)
            raise ValidationError(serializer.errors)

        self.perform_create(serializer)
        user = serializer.instance
        logger.info('user_signup_completed', user_id=user.pk, has_email=bool(user.email))

        return Response(
            {
                'username': serializer.data['username'],
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=['user : api'])
class UserAPIKeyViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return UserAPIKey.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        if self.action == 'create':
            return UserAPIKeyCreateSerializer
        return UserAPIKeySerializer

    @extend_schema(summary='Create API Key')
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        api_key, key = UserAPIKey.objects.create_key(name=serializer.validated_data['name'], user=request.user)

        return Response(
            {
                'id': api_key.id,
                'name': api_key.name,
                'api_key': key,
                'prefix': api_key.prefix,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=['user : authentication'])
class UserEmailVerificationViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    throttle_scope = 'auth_verify_email'

    @extend_schema(
        request=None,
        responses={
            200: CodeGenericResponseSerializer,
            400: CodeGenericResponseSerializer,
            401: CodeGenericResponseSerializer,
            429: CodeGenericResponseSerializer,
        },
    )
    @extend_schema(summary='Request email verification')
    @action(detail=False, methods=['post'], url_path='request-code')
    def request_code(self, request):
        user: User = request.user

        if not user.email:
            return _coded('no_email', status.HTTP_400_BAD_REQUEST)
        if user.is_email_verified:
            return _coded('already_verified', status.HTTP_400_BAD_REQUEST)

        VerificationService.create_and_send_code(user, VerificationeCodeChoices.EMAIL_VERIFICATION)
        logger.info('email_verification_requested', user_id=user.pk, reason='resend')

        return _coded('sent', status.HTTP_200_OK)

    @extend_schema(
        request=VerifyCodeSerializer,
        responses={
            200: CodeGenericResponseSerializer,
            400: CodeGenericResponseSerializer,
            401: CodeGenericResponseSerializer,
            429: CodeGenericResponseSerializer,
        },
        summary='Verify email',
    )
    @action(detail=False, methods=['post'], url_path='verify')
    def verify_email(self, request):
        serializer = VerifyCodeSerializer(data=request.data)

        if serializer.is_valid():
            code = serializer.validated_data['code']

            success, result = VerificationService.verify_code(request.user, code)

            return _coded(result, status.HTTP_200_OK if success else status.HTTP_400_BAD_REQUEST)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# Custom Token Refresh View to set last_login
@extend_schema(tags=['user : authentication'])
class CustomTokenRefreshView(TokenRefreshView):
    @extend_schema(auth=[], summary='Refresh access token')
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)

        if response.status_code == 200:
            refresh_token_str = request.data.get('refresh')
            token = RefreshToken(refresh_token_str)
            user_id = token.get('user_id', None)

            if user_id and cache.add(f'USER:post_refresh:{user_id}', 1, POST_REFRESH_INTERVAL_SECONDS):
                user_handle_post_refresh.delay(user_id)

        return response


@extend_schema(tags=['user : authentication'])
class UserPasswordResetViewSet(viewsets.ViewSet):
    permission_classes = [AllowAny]
    throttle_scope = 'auth_password_reset'

    @extend_schema(
        auth=[],
        request=PasswordResetRequestSerializer,
        responses={
            200: CodeGenericResponseSerializer,
            400: CodeGenericResponseSerializer,
            401: CodeGenericResponseSerializer,
            429: CodeGenericResponseSerializer,
        },
    )
    @extend_schema(summary='Request password reset code')
    @action(detail=False, methods=['post'])
    def request_code(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)

        serializer.is_valid(raise_exception=True)

        # only a confirmed address gets a code; the answer is the same either way (no account enumeration)
        # order_by: deterministic while case-duplicate addresses still exist (see migration 0015)
        user: User | None = (
            User.objects.filter(email__iexact=serializer.validated_data['email'], is_email_verified=True)
            .order_by('pk')
            .first()
        )
        if user:
            VerificationService.create_and_send_code(user, VerificationeCodeChoices.PASSWORD_RESET)
        logger.info('password_reset_requested', matched=user is not None, user_id=user.pk if user else None)

        return _coded('requested', status.HTTP_200_OK)

    @extend_schema(
        auth=[],
        request=PasswordResetConfirmSerializer,
        responses={
            200: CodeGenericResponseSerializer,
            400: CodeGenericResponseSerializer,
        },
        summary='Confirm password reset with code',
    )
    @action(detail=False, methods=['post'])
    def password_reset(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user, code_obj = VerificationService.get_valid_code_or_raise(data['email'], data['code'])
        VerificationService.execute_password_reset(user, code_obj, data['new_password'])

        return _coded('reset', status.HTTP_200_OK)


@extend_schema(tags=['user : profile'])
class UserProfileViewSet(viewsets.GenericViewSet):
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user

    @property
    def throttle_scope(self) -> str | None:
        # saving FIO credentials calls FIO, so it must not become a key tester
        return 'profile_update' if self.action == 'update_profile' else None

    def get_serializer_class(self):
        if self.action == 'change_password':
            return UserChangePasswordSerializer
        return UserProfileSerializer

    @extend_schema(summary='Update profile')
    @action(detail=False, methods=['patch'], url_path='update_profile')
    def update_profile(self, request):
        instance = self.get_object()
        serializer = UserProfileSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(summary='Change password')
    @action(detail=False, methods=['post'], url_path='change_password')
    def change_password(self, request):
        user = self.get_object()
        serializer = UserChangePasswordSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        # set new password
        user.set_password(serializer.validated_data['new_password'])
        user.save()

        return Response({'detail': 'Password updated successfully'}, status=status.HTTP_200_OK)

    @extend_schema(summary='Get profile password')
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = UserProfileSerializer(instance)
        return Response(serializer.data)
