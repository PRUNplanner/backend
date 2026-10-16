from datetime import timedelta

from django.http import HttpRequest
from django.utils import timezone
from rest_framework import authentication, exceptions
from rest_framework.request import Request
from rest_framework_simplejwt.authentication import JWTAuthentication

from user.models import UserAPIKey


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


class LoggedBasicAuthentication(authentication.BasicAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            _mark(request, auth='basic')
        return result
