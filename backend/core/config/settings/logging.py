import time

import orjson
import structlog
from django.dispatch import receiver
from django_structlog import signals


def orjson_renderer(_, __, event_dict):
    return orjson.dumps(event_dict, default=str).decode('utf-8')


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
        'celery': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
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

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
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
    request._custom_start_time = time.time()
    structlog.contextvars.bind_contextvars(method=request.method)


@receiver(signals.bind_extra_request_finished_metadata)
def add_request_duration(request, logger, response, **kwargs):
    start_time = getattr(request, '_custom_start_time', None)

    if start_time:
        duration_ms = (time.time() - start_time) * 1000
        structlog.contextvars.bind_contextvars(duration_ms=round(duration_ms, 2))

    if request.resolver_match:
        view_name = request.resolver_match.view_name
        structlog.contextvars.bind_contextvars(route=view_name)
    else:
        # Fallback for 404s or static files where no name exists
        structlog.contextvars.bind_contextvars(route='unknown')
