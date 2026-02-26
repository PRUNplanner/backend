from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView
from user.api.serializer import (
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
from user.tasks import user_handle_post_refresh


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
    def update(self, request, *args, **kwargs):
        # Partial = True, allows subset of fields from frontend
        partial = kwargs.pop('partial', False)
        serializer = self.get_serializer(data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        obj, _ = UserPreference.objects.get_or_create(user=request.user)
        obj.preferences = serializer.validated_data
        obj.save()

        return Response(serializer.data)


@extend_schema(tags=['user : authentication'])
class UserRegisterViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = UserRegisterSerializer
    permission_classes = [AllowAny]

    def perform_create(self, serializer):
        serializer.save()

    @extend_schema(auth=[], summary='Register account')
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        self.perform_create(serializer)

        return Response(
            {
                'detail': 'User registered successfully.',
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

        if user.is_email_verified:
            return Response({'detail': 'Email already verified.'}, status=status.HTTP_400_BAD_REQUEST)

        VerificationService.create_and_send_code(user, VerificationeCodeChoices.EMAIL_VERIFICATION)

        return Response({'detail': 'Verification code sent to your email.'}, status=status.HTTP_200_OK)

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

            success, message = VerificationService.verify_code(
                request.user, code.upper(), VerificationeCodeChoices.EMAIL_VERIFICATION
            )

            if success:
                return Response({'detail': message}, status=status.HTTP_200_OK)

            return Response({'detail': message}, status=status.HTTP_400_BAD_REQUEST)

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

            if user_id:
                user_handle_post_refresh.delay(user_id)

        return response


@extend_schema(tags=['user : authentication'])
class UserPasswordResetViewSet(viewsets.ViewSet):
    permission_classes = [AllowAny]

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

        if serializer.is_valid(raise_exception=True):
            # find user with this email
            user: User | None = User.objects.filter(email=serializer.validated_data['email']).first()

            if user:
                VerificationService.create_and_send_code(user, VerificationeCodeChoices.PASSWORD_RESET)

        return Response(
            {'detail': 'If your email is valid and attached to a PRUNplanner user, please check your inbox.'},
            status=status.HTTP_200_OK,
        )

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

        serializer.save()

        return Response(
            {'detail': 'Password successfully reset.'},
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=['user : profile'])
class UserProfileViewSet(viewsets.GenericViewSet):
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user

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
