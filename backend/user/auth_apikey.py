from datetime import timedelta

from django.utils import timezone
from rest_framework import authentication, exceptions

from user.models import UserAPIKey


class UserAPIKeyAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        key = None

        # get key from Authorization header
        header = request.META.get('HTTP_AUTHORIZATION')
        if header and 'Api-Key ' in header:
            key = header.split('Api-Key ')[1].strip()

        # fallback: try to get key from URL query parameters (?api_key=)
        if not key:
            key = request.query_params.get('api_key')

        # no key found, return None and let other auth classes check
        if not key:
            return None

        try:
            # verification
            api_key: UserAPIKey = UserAPIKey.objects.get_from_key(key)

            if api_key.revoked:
                raise exceptions.AuthenticationFailed('This API key has been revoked')

            user = api_key.user
            now = timezone.now()
            one_hour_ago = now - timedelta(hours=1)

            # update last_login / last_used only once per hour to optimize
            if user.last_login is None or user.last_login < one_hour_ago:
                user.last_login = now
                user.save(update_fields=['last_login'])

            if api_key.last_used is None or api_key.last_used < one_hour_ago:
                api_key.last_used = now
                api_key.save(update_fields=['last_used'])

            return (api_key.user, None)

        except UserAPIKey.DoesNotExist as exc:
            raise exceptions.AuthenticationFailed('Invalid API Key') from exc
