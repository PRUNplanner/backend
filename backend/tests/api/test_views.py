import uuid

import pytest
from django.core.cache.backends.locmem import LocMemCache
from django.urls import reverse
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

URL = reverse('client-errors')
# the tab's session id; reports without a valid one are dropped (backend#125)
TRACE = {'HTTP_X_CORRELATION_ID': str(uuid.uuid4())}


def report(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        'kind': 'validation',
        'method': 'GET',
        'path_template': '/planning/plan/{id}/',
        'status': 200,
        'failed_request_id': str(uuid.uuid4()),
        'issues': ['planets.0.name: invalid_type'],
        'client_ms': 120,
        'release': '1.2.3',
        'route_name': 'plan',
    }
    return body | overrides


def client_errors(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [r.msg for r in caplog.records if isinstance(r.msg, dict) and r.msg['event'] == 'client_error']


class TestClientErrorView:
    def test_report_is_logged(self, api_client: APIClient, caplog: pytest.LogCaptureFixture) -> None:
        body = report()

        response = api_client.post(URL, body, format='json', **TRACE)

        assert response.status_code == 204
        (line,) = client_errors(caplog)
        assert line['level'] == 'warning'
        assert {k: line[k] for k in body} == body

    def test_minimal_report(self, api_client: APIClient, caplog: pytest.LogCaptureFixture) -> None:
        body = {'kind': 'network', 'method': 'POST', 'path_template': '/user/login/', 'release': '1.2.3'}

        assert api_client.post(URL, body, format='json', **TRACE).status_code == 204
        assert len(client_errors(caplog)) == 1

    def test_query_loop_needs_no_call(self, api_client: APIClient, caplog: pytest.LogCaptureFixture) -> None:
        body = {'kind': 'query_loop', 'query': 'GetFIOStorage', 'fetches': 5, 'release': '1.2.3'}

        assert api_client.post(URL, body, format='json', **TRACE).status_code == 204
        (line,) = client_errors(caplog)
        assert {k: line[k] for k in body} == body

    @pytest.mark.parametrize('field', ['method', 'path_template'])
    def test_call_fields_required_for_other_kinds(self, field: str, api_client: APIClient) -> None:
        body = report()
        del body[field]

        response = api_client.post(URL, body, format='json', **TRACE)

        assert response.status_code == 400
        assert field in response.json()

    def test_expired_token_is_ignored(self, api_client: APIClient) -> None:
        response = api_client.post(
            URL, report(), format='json', **TRACE, HTTP_AUTHORIZATION='Bearer expired.junk.token'
        )

        assert response.status_code == 204

    @pytest.mark.parametrize(
        'overrides',
        [
            {'extra': 'x'},
            {'kind': 'oops'},
            {'method': 'HEAD'},
            {'path_template': 'planning/plan/'},
            {'path_template': '/' + 'a' * 200},
            {'status': 99},
            {'status': 600},
            {'failed_request_id': 'abc'},
            {'issues': ['x'] * 21},
            {'issues': ['x' * 201]},
            {'client_ms': -1},
            {'release': 'r' * 41},
            {'route_name': 'n' * 81},
            {'query': 'q' * 81},
            {'fetches': -1},
            {'fetches': 1001},
            {'kind': 'query_loop', 'query': None},
        ],
    )
    def test_invalid_report_is_rejected(
        self, overrides: dict[str, object], api_client: APIClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        response = api_client.post(URL, report(**overrides), format='json', **TRACE)

        assert response.status_code == 400
        assert client_errors(caplog) == []

    def test_throttled_after_30_a_minute(self, api_client: APIClient, locmem_cache: LocMemCache) -> None:
        for _ in range(30):
            assert api_client.post(URL, report(), format='json', **TRACE).status_code == 204

        assert api_client.post(URL, report(), format='json', **TRACE).status_code == 429

    def test_report_carries_the_correlation_id(self, api_client: APIClient, caplog: pytest.LogCaptureFixture) -> None:
        assert api_client.post(URL, report(), format='json', **TRACE).status_code == 204

        (line,) = client_errors(caplog)
        assert line['correlation_id'] == TRACE['HTTP_X_CORRELATION_ID']

    @pytest.mark.parametrize('headers', [{}, {'HTTP_X_CORRELATION_ID': 'not-a-uuid'}])
    @pytest.mark.parametrize('body', [report(), {'release': '0.31.0'}])
    def test_report_without_correlation_id_is_dropped(
        self, headers: dict[str, str], body: dict[str, object], api_client: APIClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        response = api_client.post(URL, body, format='json', **headers)

        assert response.status_code == 204
        assert client_errors(caplog) == []
