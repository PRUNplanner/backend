from datetime import timedelta

from django.http import HttpRequest
from django.utils import timezone
from rest_framework import authentication, exceptions
from rest_framework.request import Request
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken
from rest_framework_simplejwt.serializers import TokenRefreshSerializer as SimpleJWTTokenRefreshSerializer
from rest_framework_simplejwt.tokens import Token

from user.models import User, UserAPIKey


def _mark(request: Request, **fields: str) -> None:
    # on the Django request, which add_request_duration (core/config/settings/logging.py) logs on request_finished
    django_request: HttpRequest = request._request
    for name, value in fields.items():
        setattr(django_request, f'_{name}', value)


class UserAPIKeyAuthentication(authentication.BaseAuthentication):
    LAST_USED_INTERVAL = timedelta(minutes=5)

    def authenticate(self, request):
        key = None
        via = 'header'

        # get key from Authorization header
        header = request.META.get('HTTP_AUTHORIZATION')
        if header and 'Api-Key ' in header:
            key = header.split('Api-Key ')[1].strip()

        # fallback: try to get key from URL query parameters (?api_key=)
        if not key:
            key = request.query_params.get('api_key')
            via = 'query'

        # no key found, return None and let other auth classes check
        if not key:
            return None

        try:
            # verification
            api_key: UserAPIKey = UserAPIKey.objects.get_from_key(key)

            user = api_key.user
            # a deactivated user's, an expired key or one from before the token cutoff reads as an unknown key
            if not user.is_active or api_key.has_expired or user.issued_before_cutoff(api_key.created.timestamp()):
                raise UserAPIKey.DoesNotExist

            now = timezone.now()
            one_hour_ago = now - timedelta(hours=1)

            # update last_login / last_used only once per hour to optimize
            if user.last_login is None or user.last_login < one_hour_ago:
                user.last_login = now
                user.save(update_fields=['last_login'])

            # update apikey last_used at most every few minutes
            if api_key.last_used is None or api_key.last_used < now - self.LAST_USED_INTERVAL:
                api_key.last_used = now
                api_key.save(update_fields=['last_used'])

            # the prefix is the non-secret part shown on the API Keys page
            _mark(request, auth='api_key', api_key_prefix=api_key.prefix, api_key_via=via)
            return (api_key.user, None)

        except UserAPIKey.DoesNotExist as exc:
            # get_from_key only searches usable keys; tell a revoked key from an unknown one (failures only)
            revoked = UserAPIKey.objects.filter(prefix=key.partition('.')[0], revoked=True).first()
            if revoked is not None and revoked.is_valid(key):
                _mark(request, auth_error='api_key_revoked')
                raise exceptions.AuthenticationFailed('This API key has been revoked') from exc

            _mark(request, auth_error='api_key_invalid')
            raise exceptions.AuthenticationFailed('Invalid API Key') from exc


class LoggedJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            _mark(request, auth='jwt')
        return result

    def get_user(self, validated_token: Token) -> User:
        # the row super() loads anyway, so the cutoff costs no query
        user = super().get_user(validated_token)
        if user.issued_before_cutoff(validated_token['iat']):
            raise InvalidToken()
        return user


# refresh that honours User.tokens_valid_after, and a deleted user is a 401, not a 500
# (a comment, not a docstring: spectacular would publish it in the schema)
class TokenRefreshSerializer(SimpleJWTTokenRefreshSerializer):
    def validate(self, attrs: dict[str, str]) -> dict[str, str]:
        refresh = self.token_class(attrs['refresh'])  # ty: ignore[invalid-argument-type]  # simplejwt's hint omits str
        user = User.objects.filter(pk=refresh.payload.get('user_id')).first()
        if user is None or not user.is_active:
            raise AuthenticationFailed(self.error_messages['no_active_account'], 'no_active_account')
        if user.issued_before_cutoff(refresh['iat']):
            raise InvalidToken()
        # ponytail: replaces super().validate(), which would load the user again; no ROTATE_REFRESH_TOKENS
        # support (off in SIMPLE_JWT), call super() instead if rotation is ever turned on
        return {'access': str(refresh.access_token)}
