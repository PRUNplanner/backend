import logging
import sys
import uuid
from types import SimpleNamespace

import orjson
import pytest
import structlog
from core.config.settings.logging import drop_duplicate_events, redact_webhook_token
from django.urls import reverse
from django_structlog import signals
from django_structlog.celery import signals as celery_signals
from rest_framework.test import APIClient

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

        logged = redact_webhook_token(None, 'info', {'request': f'POST {path}'})

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


class TestTaskLog:
    def test_task_name_is_bound_for_every_line(self) -> None:
        structlog.contextvars.clear_contextvars()
        try:
            celery_signals.bind_extra_task_metadata.send(
                sender=None, task=SimpleNamespace(name='gamedata_refresh_cxpc'), logger=None
            )
            assert structlog.contextvars.get_contextvars()['task'] == 'gamedata_refresh_cxpc'
        finally:
            structlog.contextvars.clear_contextvars()
