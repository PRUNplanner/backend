import structlog
from django.http import HttpRequest, HttpResponse
from django.template import loader
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from api.serializer import ClientErrorSerializer

logger = structlog.get_logger(__name__)


def index(request: HttpRequest) -> HttpResponse:
    template = loader.get_template('api/index.html')

    return HttpResponse(template.render(None, request))


class ClientErrorView(APIView):
    """Error reports from the frontend, logged as client_error (joined to the failed call by failed_request_id)."""

    # no authentication: an expired token must not turn a report into a 401
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = 'client_error'

    @extend_schema(auth=[], summary='Report a frontend error', request=ClientErrorSerializer, responses={204: None})
    def post(self, request: Request) -> Response:
        serializer = ClientErrorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        if data.get('failed_request_id') is not None:
            data['failed_request_id'] = str(data['failed_request_id'])
        logger.warning('client_error', **data)
        return Response(status=204)
