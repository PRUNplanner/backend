import base64
import logging
import sys
import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import orjson
import pytest
import structlog
from asgiref.sync import async_to_sync
from core.config.settings.logging import drop_duplicate_events, redact_secrets
from django.db import connection
from django.test import AsyncClient
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django_structlog import signals
from django_structlog.celery import signals as celery_signals
from django_structlog.celery.receivers import CeleryReceiver
from gamedata.models import GameMaterial
from gamedata.tasks import gamedata_clean_user_fiodata
from model_bakery import baker
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken
from user.models import UserAPIKey

pytestmark = pytest.mark.django_db


def events(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str]]:
    """(event, level) of every structlog line; wrap_for_formatter leaves the event dict in record.msg."""
    return [(r.msg['event'], r.levelname) for r in caplog.records if isinstance(r.msg, dict)]


class TestProcessors:
    @pytest.mark.parametrize('event', ['request_started', 'task_enqueued', 'streaming_cancelled'])
    def test_duplicate_events_are_dropped(self, event: str) -> None:
        with pytest.raises(structlog.DropEvent):
            drop_duplicate_events(None, 'info', {'event': event})

    def test_other_events_pass(self) -> None:
        event_dict = {'event': 'request_finished'}
        assert drop_duplicate_events(None, 'info', event_dict) is event_dict

    def test_webhook_token_is_redacted(self) -> None:
        token = uuid.uuid4()
        path = reverse('data:fio-webhook-ingest', kwargs={'token': token})

        logged = redact_secrets(None, 'info', {'request': f'POST {path}'})

        assert str(token) not in logged['request']
        assert logged['request'].endswith('/ingest/***')

    def test_stdlib_record_renders_its_traceback(self) -> None:
        # the formatter Django installed from LOGGING on the root console handler
        formatter = next(
            h.formatter
            for h in logging.getLogger().handlers
            if isinstance(h.formatter, structlog.stdlib.ProcessorFormatter)
        )
        try:
            raise ZeroDivisionError('boom')
        except ZeroDivisionError:
            record = logging.LogRecord(
                'django.request', logging.ERROR, __file__, 1, 'Internal Server Error: %s', ('/x',), sys.exc_info()
            )

        line = orjson.loads(formatter.format(record))

        assert line['event'] == 'Internal Server Error: /x'
        assert 'Traceback' in line['exception']
        assert 'ZeroDivisionError: boom' in line['exception']
        assert 'exc_info' not in line


class TestRequestLog:
    def test_one_line_per_request_with_route_duration_and_user_agent(
        self, api_client: APIClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        seen: list[tuple[dict[str, object], dict[str, object]]] = []

        def snapshot(log_kwargs: dict[str, object], **_: object) -> None:
            seen.append((dict(log_kwargs), structlog.contextvars.get_contextvars()))

        signals.bind_extra_request_finished_metadata.connect(snapshot, weak=False)
        try:
            api_client.get(reverse('data:material-list'), HTTP_USER_AGENT='pytest-agent')
        finally:
            signals.bind_extra_request_finished_metadata.disconnect(snapshot)

        log_kwargs, context = seen[0]
        assert log_kwargs['user_agent'] == 'pytest-agent'
        assert context['route'] == 'data:material-list'
        assert isinstance(context['duration_ms'], float)
        assert [e for e, _ in events(caplog) if e.startswith('request_')] == ['request_finished']

    def test_client_errors_log_at_info(self, api_client: APIClient, caplog: pytest.LogCaptureFixture) -> None:
        api_client.get('/no-such-route/')

        assert ('request_finished', 'INFO') in events(caplog)


class TestRequestDbStats:
    @staticmethod
    def finished(caplog: pytest.LogCaptureFixture) -> dict[str, object]:
        (line,) = [r.msg for r in caplog.records if isinstance(r.msg, dict) and r.msg['event'] == 'request_finished']
        return line

    def test_request_logs_its_query_count_and_time(
        self, api_client: APIClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        baker.make('gamedata.GameMaterial', _quantity=2)

        with CaptureQueriesContext(connection) as queries:
            api_client.get(reverse('data:material-list'))

        line = self.finished(caplog)
        assert line['db_queries'] == len(queries) > 0
        assert isinstance(line['db_ms'], float)

    def test_sync_view_under_asgi_is_counted(self, caplog: pytest.LogCaptureFixture) -> None:
        # the ASGI handler runs the middleware and the sync DRF view through sync_to_async, each call with a
        # copy of the context; the counter has to survive that
        baker.make('gamedata.GameMaterial', _quantity=2)

        with CaptureQueriesContext(connection) as queries:
            response = async_to_sync(AsyncClient().get)(reverse('data:material-list'))

        assert response.status_code == 200
        assert self.finished(caplog)['db_queries'] == len(queries) > 0

    def test_each_request_starts_at_zero(self, api_client: APIClient, caplog: pytest.LogCaptureFixture) -> None:
        api_client.get(reverse('data:material-list'))
        first = self.finished(caplog)['db_queries']
        caplog.clear()

        api_client.get(reverse('data:material-list'))

        assert self.finished(caplog)['db_queries'] == first


class TestTaskLog:
    @pytest.fixture(autouse=True)
    def clean_context(self) -> Iterator[None]:
        structlog.contextvars.clear_contextvars()
        yield
        structlog.contextvars.clear_contextvars()

    @staticmethod
    def start(request: SimpleNamespace) -> dict[str, object]:
        """Context bound for a task starting with this request."""
        celery_signals.bind_extra_task_metadata.send(
            sender=None, task=SimpleNamespace(name='gamedata_refresh_cxpc', request=request), logger=None
        )
        return structlog.contextvars.get_contextvars()

    def test_task_name_is_bound_for_every_line(self) -> None:
        assert self.start(SimpleNamespace())['task'] == 'gamedata_refresh_cxpc'

    def test_published_task_logs_its_queue_wait(self) -> None:
        # a mock producer: the publish signals fire, nothing reaches a broker. AsyncResult would load the result
        # backend, which CI does not configure.
        producer = MagicMock()
        with patch.object(gamedata_clean_user_fiodata, 'AsyncResult'):
            gamedata_clean_user_fiodata.apply_async(args=[1], producer=producer)
        headers = producer.publish.call_args.kwargs['headers']

        with patch('core.config.settings.logging.time.time', return_value=headers['published_at_ms'] / 1000 + 1.5):
            # the worker exposes message headers as attributes of task.request
            context = self.start(SimpleNamespace(**headers))

        assert context['queue_ms'] == 1500

    def test_task_without_publish_time_has_no_queue_wait(self) -> None:
        assert 'queue_ms' not in self.start(SimpleNamespace())

    def test_task_logs_its_own_query_count(self, caplog: pytest.LogCaptureFixture) -> None:
        receiver = CeleryReceiver()

        def run(name: str, queries: int) -> dict[str, object]:
            task = type(name, (), {'name': name, 'request': SimpleNamespace()})
            caplog.clear()
            receiver.receiver_task_prerun(task_id=name, task=task)
            for _ in range(queries):
                GameMaterial.objects.count()
            if name == 'fails':
                receiver.receiver_task_failure(task_id=name, exception=ValueError('boom'), sender=task)
            else:
                receiver.receiver_task_success(result=None, sender=task)
            (line,) = [
                r.msg
                for r in caplog.records
                if isinstance(r.msg, dict) and r.msg['event'] in ('task_succeeded', 'task_failed')
            ]
            return line

        assert run('first', 3)['db_queries'] == 3
        # not the previous task's
        assert run('second', 1)['db_queries'] == 1
        failed = run('fails', 2)
        assert (failed['event'], failed['db_queries']) == ('task_failed', 2)
        assert isinstance(failed['db_ms'], float)


class TestRedaction:
    @pytest.mark.parametrize(
        ('logged', 'expected'),
        [
            ('GET /data/exchanges/csv/?foo=1&api_key=SECRET', 'GET /data/exchanges/csv/?foo=1&api_key=***'),
            ('GET /csv/exchange?api_key=SECRET', 'GET /csv/exchange?api_key=***'),
            ('GET /csv/exchange?API_KEY=SECRET&foo=1', 'GET /csv/exchange?API_KEY=***&foo=1'),
            ('GET /data/storage/?my_api_key=1', 'GET /data/storage/?my_api_key=1'),
        ],
    )
    def test_api_key_query_value_is_redacted(self, logged: str, expected: str) -> None:
        assert redact_secrets(None, 'info', {'request': logged, 'url': logged}) == {
            'request': expected,
            'url': expected,
        }


class TestRequestAuthLog:
    @staticmethod
    def finished(caplog: pytest.LogCaptureFixture) -> dict[str, object]:
        (line,) = [r.msg for r in caplog.records if isinstance(r.msg, dict) and r.msg['event'] == 'request_finished']
        return line

    @pytest.fixture
    def key(self) -> tuple[UserAPIKey, str]:
        return UserAPIKey.objects.create_key(name='script', user=baker.make('user.User'))

    def test_public_request_logs_no_auth(self, api_client: APIClient, caplog: pytest.LogCaptureFixture) -> None:
        api_client.get(reverse('data:material-list'))

        line = self.finished(caplog)
        assert line['auth'] == 'none'
        assert 'api_key_prefix' not in line

    def test_jwt_request(self, api_client: APIClient, caplog: pytest.LogCaptureFixture) -> None:
        token = AccessToken.for_user(baker.make('user.User'))

        response = api_client.get(reverse('planning:empire'), HTTP_AUTHORIZATION=f'Bearer {token}')

        assert response.status_code == 200
        assert self.finished(caplog)['auth'] == 'jwt'

    def test_basic_request(self, api_client: APIClient, caplog: pytest.LogCaptureFixture) -> None:
        user = baker.make('user.User', username='basic')
        user.set_password('pw')
        user.save()
        credentials = base64.b64encode(b'basic:pw').decode()

        response = api_client.get(reverse('planning:empire'), HTTP_AUTHORIZATION=f'Basic {credentials}')

        assert response.status_code == 200
        assert self.finished(caplog)['auth'] == 'basic'

    def test_api_key_in_header(
        self, api_client: APIClient, caplog: pytest.LogCaptureFixture, key: tuple[UserAPIKey, str]
    ) -> None:
        api_key, plain = key

        response = api_client.get(reverse('planning:empire'), HTTP_AUTHORIZATION=f'Api-Key {plain}')

        assert response.status_code == 200
        line = self.finished(caplog)
        assert (line['auth'], line['api_key_prefix'], line['api_key_via']) == ('api_key', api_key.prefix, 'header')
        assert plain not in caplog.text

    def test_api_key_in_query_is_redacted(
        self, api_client: APIClient, caplog: pytest.LogCaptureFixture, key: tuple[UserAPIKey, str]
    ) -> None:
        api_key, plain = key

        response = api_client.get(reverse('planning:empire'), {'foo': '1', 'api_key': plain})

        assert response.status_code == 200
        line = self.finished(caplog)
        assert (line['auth'], line['api_key_prefix'], line['api_key_via']) == ('api_key', api_key.prefix, 'query')
        assert line['request'] == f'GET {reverse("planning:empire")}?foo=1&api_key=***'
        assert plain not in caplog.text

    def test_unknown_key(self, api_client: APIClient, caplog: pytest.LogCaptureFixture) -> None:
        response = api_client.get(reverse('planning:empire'), {'api_key': 'SECRET.unknown'})

        assert response.status_code == 401
        line = self.finished(caplog)
        assert (line['auth'], line['auth_error']) == ('none', 'api_key_invalid')
        assert 'SECRET' not in caplog.text

    def test_revoked_key(
        self, api_client: APIClient, caplog: pytest.LogCaptureFixture, key: tuple[UserAPIKey, str]
    ) -> None:
        api_key, plain = key
        UserAPIKey.objects.filter(pk=api_key.pk).update(revoked=True)

        response = api_client.get(reverse('planning:empire'), HTTP_AUTHORIZATION=f'Api-Key {plain}')

        assert response.status_code == 401
        assert response.data['detail'] == 'This API key has been revoked'
        assert self.finished(caplog)['auth_error'] == 'api_key_revoked'
