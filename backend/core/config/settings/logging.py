import logging
import re
import time

import orjson
import structlog
from django.dispatch import receiver
from django_structlog import signals
from django_structlog.celery import signals as celery_signals

# propagate request_id/user_id into the tasks a request queues
DJANGO_STRUCTLOG_CELERY_ENABLED = True
# expired tokens and bot 404s are routine; warning and error are kept for what needs a look
DJANGO_STRUCTLOG_STATUS_4XX_LOG_LEVEL = logging.INFO

# request_finished and task_started repeat the first two, sse_stream_opened/closed the streaming ones
# (the SSE view is the only streaming response)
_DROPPED_EVENTS = frozenset(
    {'request_started', 'task_enqueued', 'streaming_started', 'streaming_finished', 'streaming_cancelled'}
)
# the FIO webhook authenticates by a token in its path
_WEBHOOK_TOKEN = re.compile(r'(/ingest/)[0-9a-fA-F-]{36}')


def orjson_renderer(_, __, event_dict):
    return orjson.dumps(event_dict, default=str).decode('utf-8')


def drop_duplicate_events(_, __, event_dict):
    if event_dict.get('event') in _DROPPED_EVENTS:
        raise structlog.DropEvent
    return event_dict


def redact_webhook_token(_, __, event_dict):
    request = event_dict.get('request')
    if isinstance(request, str):
        event_dict['request'] = _WEBHOOK_TOKEN.sub(r'\1***', request)
    return event_dict


LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'json_formatter': {
            '()': structlog.stdlib.ProcessorFormatter,
            'processor': orjson_renderer,
            'foreign_pre_chain': [
                structlog.contextvars.merge_contextvars,
                structlog.processors.TimeStamper(fmt='iso'),
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
                structlog.stdlib.PositionalArgumentsFormatter(),
                # stdlib records (django.request, celery.app.trace) carry exc_info as a tuple, render the traceback
                structlog.processors.format_exc_info,
            ],
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'json_formatter',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        # 4xx warnings repeat request_finished; 5xx stay
        'django.request': {'level': 'ERROR'},
        'celery': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        # "Received task" and "Task ... succeeded" repeat django_structlog's task_started and task_succeeded
        'celery.app.trace': {'level': 'WARNING'},
        'celery.worker.strategy': {'level': 'WARNING'},
        # "HTTP Request: GET ..." repeats fio_request_completed
        'httpx': {'level': 'WARNING'},
        'gunicorn.error': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
}

# warnings.warn() output goes through the JSON formatter as logger py.warnings, not raw to stderr
logging.captureWarnings(True)

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        drop_duplicate_events,
        redact_webhook_token,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.TimeStamper(fmt='iso'),
        structlog.processors.CallsiteParameterAdder({structlog.processors.CallsiteParameter.PROCESS}),
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
    wrapper_class=structlog.stdlib.BoundLogger,
)


@receiver(signals.bind_extra_request_metadata)
def mark_request_start_time(request, **kwargs):
    request._custom_start_time = time.perf_counter()
    structlog.contextvars.bind_contextvars(method=request.method)


@receiver(signals.bind_extra_request_finished_metadata)
def add_request_duration(request, logger, response, log_kwargs, **kwargs):
    start_time = getattr(request, '_custom_start_time', None)

    if start_time:
        duration_ms = (time.perf_counter() - start_time) * 1000
        structlog.contextvars.bind_contextvars(duration_ms=round(duration_ms, 2))

    # was on request_started, which is dropped
    log_kwargs['user_agent'] = request.META.get('HTTP_USER_AGENT')

    if request.resolver_match:
        view_name = request.resolver_match.view_name
        structlog.contextvars.bind_contextvars(route=view_name)
    else:  # pragma: no cover
        # Fallback for 404s or static files where no name exists
        structlog.contextvars.bind_contextvars(route='unknown')


@receiver(celery_signals.bind_extra_task_metadata)
def bind_task_name(task, **kwargs):
    # django_structlog only names the task on task_started; bind it for every line the task logs
    structlog.contextvars.bind_contextvars(task=task.name)
