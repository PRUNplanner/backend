from typing import TYPE_CHECKING

import structlog
from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle, UserRateThrottle

# type-only: rest_framework.views imports the throttle classes while it loads
if TYPE_CHECKING:
    from rest_framework.request import Request
    from rest_framework.views import APIView

logger = structlog.get_logger(__name__)


def _log_throttled(throttle: SimpleRateThrottle, request: 'Request') -> None:
    # once per throttle key and window, so a loop doesn't flood Axiom with a line per request
    if not throttle.cache.add(f'throttle-logged:{throttle.key}', 1, throttle.duration):
        return
    django_request = request._request
    resolver_match = django_request.resolver_match
    logger.warning(
        'rate_limited',
        scope=throttle.scope,
        user_id=request.user.pk if request.user.is_authenticated else None,
        auth=getattr(django_request, '_auth', 'none'),
        # with the leading slash, as client_error's path_template
        path_template=f'/{resolver_match.route}' if resolver_match else None,
        rate=throttle.rate,
    )


class LoggedScopedRateThrottle(ScopedRateThrottle):
    """ScopedRateThrottle that logs rate_limited when it refuses a request."""

    def allow_request(self, request: 'Request', view: 'APIView') -> bool:
        if super().allow_request(request, view):
            return True
        _log_throttled(self, request)
        return False


class AuthenticatedUserRateThrottle(UserRateThrottle):
    """
    Per-user ceiling on every request (scope `user`). Anonymous requests aren't counted; JWT, API key and
    basic auth of one user share the count, keyed by user id.
    """

    def get_cache_key(self, request: 'Request', view: 'APIView') -> str | None:
        if not request.user.is_authenticated:
            return None
        return self.cache_format % {'scope': self.scope, 'ident': request.user.pk}

    def allow_request(self, request: 'Request', view: 'APIView') -> bool:
        if super().allow_request(request, view):
            return True
        _log_throttled(self, request)
        return False
