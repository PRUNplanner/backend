import uuid
from unittest.mock import MagicMock, patch

import pytest
import structlog
from django.urls import reverse
from gamedata.tasks import gamedata_clean_user_fiodata
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


def finished(caplog: pytest.LogCaptureFixture) -> dict[str, object]:
    (line,) = [r.msg for r in caplog.records if isinstance(r.msg, dict) and r.msg['event'] == 'request_finished']
    return line


class TestSanitizeTraceHeaders:
    def test_uuid_headers_become_request_and_correlation_id(
        self, api_client: APIClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        request_id, correlation_id = str(uuid.uuid4()), str(uuid.uuid4())

        api_client.get(
            reverse('data:material-list'), HTTP_X_REQUEST_ID=request_id, HTTP_X_CORRELATION_ID=correlation_id
        )

        line = finished(caplog)
        assert (line['request_id'], line['correlation_id']) == (request_id, correlation_id)

    def test_uppercase_uuid_is_logged_lowercase(self, api_client: APIClient, caplog: pytest.LogCaptureFixture) -> None:
        value = str(uuid.uuid4())

        api_client.get(
            reverse('data:material-list'), HTTP_X_REQUEST_ID=value.upper(), HTTP_X_CORRELATION_ID=value.upper()
        )

        line = finished(caplog)
        assert (line['request_id'], line['correlation_id']) == (value, value)

    @pytest.mark.parametrize(
        'value',
        [
            'not-a-uuid',
            'x' * 500,
            f'{{{uuid.uuid4()}}}',
            f'urn:uuid:{uuid.uuid4()}',
            uuid.uuid4().hex,
        ],
    )
    def test_other_values_are_ignored(
        self, value: str, api_client: APIClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        api_client.get(reverse('data:material-list'), HTTP_X_REQUEST_ID=value, HTTP_X_CORRELATION_ID=value)

        line = finished(caplog)
        assert line['request_id'] != value
        uuid.UUID(str(line['request_id']))
        assert 'correlation_id' not in line

    def test_without_headers_a_request_id_is_generated(
        self, api_client: APIClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        api_client.get(reverse('data:material-list'))

        line = finished(caplog)
        uuid.UUID(str(line['request_id']))
        assert 'correlation_id' not in line


class TestCeleryPropagation:
    def test_queued_task_carries_both_ids(self) -> None:
        # what RequestMiddleware binds; django_structlog's before_task_publish copies it into the task headers
        producer = MagicMock()
        structlog.contextvars.bind_contextvars(request_id='r-1', correlation_id='c-1')
        try:
            with patch.object(gamedata_clean_user_fiodata, 'AsyncResult'):
                gamedata_clean_user_fiodata.apply_async(args=[1], producer=producer)
        finally:
            structlog.contextvars.clear_contextvars()

        context = producer.publish.call_args.kwargs['headers']['__django_structlog__']
        assert (context['request_id'], context['correlation_id']) == ('r-1', 'c-1')


class TestCors:
    def test_preflight_allows_trace_headers(self, api_client: APIClient) -> None:
        response = api_client.options(
            reverse('data:material-list'),
            HTTP_ORIGIN='https://prunplanner.org',
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='GET',
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS='x-request-id, x-correlation-id',
        )

        allowed = response['Access-Control-Allow-Headers']
        assert 'x-request-id' in allowed
        assert 'x-correlation-id' in allowed
