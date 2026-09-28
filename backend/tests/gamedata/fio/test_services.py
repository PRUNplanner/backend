import httpx
import pytest
from gamedata.fio.schemas.fio_planet import FIOPlanetSchema
from gamedata.fio.services import FIOURL, FIOService, get_fio_service
from pydantic import ValidationError


class TestFIOURL:
    def test_get_url(self):

        assert FIOURL.FIO_BASE_URL == 'https://rest.fnar.net/'
        assert FIOURL.get_url('allrecipes') == 'https://rest.fnar.net/recipes/allrecipes'

    def test_get_timeout(self):

        assert FIOURL.get_timeout('allrecipes') == FIOURL.endpoint_timeouts['allrecipes']


class TestFIOService:
    def test_fio_get_auth_headers(self):
        service = FIOService()
        headers = service._get_auth_headers('  key123\n\t ')
        assert headers['Authorization'] == 'key123'
        assert headers['X-FIO-Application'] == 'PRUNplanner'

    def test_get_planet_real_integration(self, httpx_mock, montem_raw_bytes):

        planet_natural_id = 'OT-580b'

        httpx_mock.add_response(
            method='GET',
            url=f'https://rest.fnar.net/planet/{planet_natural_id}',
            content=montem_raw_bytes,
            status_code=200,
        )

        with get_fio_service() as service:
            planet = service.get_planet(planet_natural_id)

            assert isinstance(planet, FIOPlanetSchema)
            assert planet.planet_name == 'Montem'
            assert planet.planet_natural_id == 'OT-580b'

            assert len(planet.resources) > 0
            assert planet.resources[0].material_id is not None

    def test_get_planet_http_error(self, httpx_mock):
        httpx_mock.add_response(status_code=500)

        with get_fio_service() as service:
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                service.get_planet('ANY')

            assert exc_info.value.response.status_code == 500

    def test_auth_header_cleaning(self):
        with get_fio_service() as service:
            dirty_key = '  abc-123\n\t '
            headers = service._get_auth_headers(apikey=dirty_key)

            assert headers['Authorization'] == 'abc-123'
            assert ' ' not in headers['Authorization']

    def test_get_planet_serialization_error(self, httpx_mock):
        from pydantic import ValidationError

        httpx_mock.add_response(
            url='https://rest.fnar.net/planet/OT-580b', json={'invalid_field': 'data'}, status_code=200
        )

        with get_fio_service() as service:
            with pytest.raises(ValidationError):
                service.get_planet('OT-580b')

    SERVICE_TEST_CASES = [
        ('get_all_materials', [], []),
        ('get_all_buildings', [], []),
        (
            'get_planet',
            ['OT-580b'],
            'USE_FIXTURE',
        ),
        ('get_all_planets', [], []),
        ('get_planet_infrastructure', ['OT-580b'], {'InfrastructureReports': []}),
        ('get_all_exchanges', [], []),
        ('get_full_exchanges', [], []),
        ('get_cxpc', ['FE', 'AIC'], []),
        ('get_all_recipes', [], []),
        ('get_user_storage', ['foo', 'key_123'], []),
        ('get_user_sites', ['foo', 'key_123'], []),
        ('get_user_sites_warehouses', ['foo', 'key_123'], []),
        ('get_user_ships', ['foo', 'key_123'], []),
    ]

    @pytest.mark.parametrize('method_name, args, mock_response', SERVICE_TEST_CASES)
    def test_fio_service_endpoints(self, httpx_mock, method_name, args, mock_response, montem_raw_bytes):

        if mock_response == 'USE_FIXTURE':
            response_kwargs = {'content': montem_raw_bytes}
        else:
            response_kwargs = {'json': mock_response}

        httpx_mock.add_response(status_code=200, **response_kwargs)

        with get_fio_service() as service:
            method = getattr(service, method_name)
            result = method(*args)

            assert result is not None

            assert len(httpx_mock.get_requests()) == 1

            request = httpx_mock.get_requests()[0]
            assert 'X-FIO-Application' in request.headers
            assert request.headers['X-FIO-Application'] == 'PRUNplanner'

    @pytest.mark.parametrize(
        'since_ms, url',
        [
            (None, 'https://rest.fnar.net/exchange/cxpc/RAT.NC1'),
            (1_790_294_400_000, 'https://rest.fnar.net/exchange/cxpc/RAT.NC1/1790294400000'),
        ],
    )
    def test_get_cxpc_url(self, httpx_mock, since_ms: int | None, url: str) -> None:
        httpx_mock.add_response(url=url, json=[])

        with get_fio_service() as service:
            assert service.get_cxpc('RAT', 'NC1', since_ms) == []

    def test_fio_service_error_handling_parameterized(self, httpx_mock):
        """Verifies all methods raise HTTPStatusError on 500 responses."""
        httpx_mock.add_response(status_code=500)

        with get_fio_service() as service:
            with pytest.raises(httpx.HTTPStatusError):
                service.get_all_materials()


class TestFIOServiceConnectionReuse:
    def test_services_share_one_open_client(self):
        with get_fio_service() as first:
            first_client = first.client
        with get_fio_service() as second:
            second_client = second.client

        assert second_client is first_client
        assert not second_client.is_closed


class TestFIOServiceLogging:
    @staticmethod
    def events(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str, object]]:
        return [
            (r.msg['event'], r.levelname, r.msg.get('status_code')) for r in caplog.records if isinstance(r.msg, dict)
        ]

    def test_error_status_logs_one_completed_line(self, httpx_mock, caplog: pytest.LogCaptureFixture) -> None:
        httpx_mock.add_response(status_code=401)

        with get_fio_service() as service, pytest.raises(httpx.HTTPStatusError):
            service.get_planet('ANY')

        assert self.events(caplog) == [('fio_request_completed', 'INFO', 401)]

    def test_no_response_logs_a_failed_warning(self, httpx_mock, caplog: pytest.LogCaptureFixture) -> None:
        httpx_mock.add_exception(httpx.ReadTimeout('slow'))

        with get_fio_service() as service, pytest.raises(httpx.ReadTimeout):
            service.get_planet('ANY')

        assert self.events(caplog) == [('fio_request_failed', 'WARNING', None)]

    def test_success_skips_the_httpx_line(self, httpx_mock, caplog: pytest.LogCaptureFixture) -> None:
        httpx_mock.add_response(status_code=200, json=[])

        with get_fio_service() as service:
            service.get_all_materials()

        assert [r for r in caplog.records if r.name == 'httpx'] == []

    def test_serialization_error_logs_the_message_without_traceback(
        self, httpx_mock, caplog: pytest.LogCaptureFixture
    ) -> None:
        httpx_mock.add_response(json={'invalid_field': 'data'})

        with get_fio_service() as service, pytest.raises(ValidationError):
            service.get_planet('OT-580b')

        [record] = [
            r for r in caplog.records if isinstance(r.msg, dict) and r.msg['event'] == 'fio_serialization_failed'
        ]
        assert record.exc_info is None
        assert isinstance(record.msg, dict)
        assert 'validation error' in record.msg['error']
