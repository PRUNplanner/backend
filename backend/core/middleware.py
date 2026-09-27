from collections.abc import Callable

from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.utils.cache import patch_cache_control


def default_cache_control(get_response: Callable[[HttpRequest], HttpResponseBase]) -> Callable[..., HttpResponseBase]:
    """Responses that don't say otherwise must be revalidated and never land in a shared cache."""

    def middleware(request: HttpRequest) -> HttpResponseBase:
        response = get_response(request)
        if not response.has_header('Cache-Control'):
            patch_cache_control(response, private=True, no_cache=True)
        return response

    return middleware
