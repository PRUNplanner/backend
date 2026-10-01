import uuid
from collections.abc import Callable

from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.utils.cache import patch_cache_control

# set by the frontend; django_structlog logs them as request_id / correlation_id
_TRACE_HEADERS = ('HTTP_X_REQUEST_ID', 'HTTP_X_CORRELATION_ID')


def _canonical_uuid(value: str) -> str | None:
    # UUID() also takes braces, urn: and undashed forms; only the 36-char dashed one is accepted, and logged in
    # the lowercase form str(UUID) gives, so it joins with failed_request_id in Axiom
    if len(value) != 36:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def sanitize_trace_headers(get_response: Callable[[HttpRequest], HttpResponseBase]) -> Callable[..., HttpResponseBase]:
    """Drops X-Request-ID / X-Correlation-ID unless they are UUIDs, so clients can't put arbitrary text in logs."""

    def middleware(request: HttpRequest) -> HttpResponseBase:
        for key in _TRACE_HEADERS:
            if key not in request.META:
                continue
            canonical = _canonical_uuid(request.META[key])
            if canonical is None:
                del request.META[key]
            else:
                request.META[key] = canonical
        return get_response(request)

    return middleware


def default_cache_control(get_response: Callable[[HttpRequest], HttpResponseBase]) -> Callable[..., HttpResponseBase]:
    """Responses that don't say otherwise must be revalidated and never land in a shared cache."""

    def middleware(request: HttpRequest) -> HttpResponseBase:
        response = get_response(request)
        if not response.has_header('Cache-Control'):
            patch_cache_control(response, private=True, no_cache=True)
        return response

    return middleware
