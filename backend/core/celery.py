import logging.config

import structlog
from celery import Celery
from celery.signals import setup_logging, worker_process_init, worker_process_shutdown
from django_structlog.celery.steps import DjangoStructLogInitStep

app = Celery('prunplanner')


app.steps['worker'].add(DjangoStructLogInitStep)  # ty:ignore[not-subscriptable]

app.config_from_object('django.conf:settings', namespace='CELERY')


@setup_logging.connect
def config_loggers(*args, **kwargs):
    from django.conf import settings

    logging.config.dictConfig(settings.LOGGING)


logger = structlog.get_logger()


@worker_process_init.connect
def log_new_process(**kwargs):
    logger.info('worker_child_process_spawned')


@worker_process_shutdown.connect
def log_shutdown_process(**kwargs):
    logger.info('worker_child_process_shutdown')


app.autodiscover_tasks()
