import logging.config

import structlog
from celery import Celery
from celery.signals import setup_logging, task_postrun, task_prerun, worker_process_init, worker_process_shutdown
from django_structlog.celery.steps import DjangoStructLogInitStep

from core.services import task_health

app = Celery('prunplanner')


app.steps['worker'].add(DjangoStructLogInitStep)  # ty:ignore[not-subscriptable]

app.config_from_object('django.conf:settings', namespace='CELERY')


@setup_logging.connect
def config_loggers(*args, **kwargs):  # pragma: no cover
    from django.conf import settings

    logging.config.dictConfig(settings.LOGGING)


logger = structlog.get_logger()


@worker_process_init.connect
def log_new_process(**kwargs):  # pragma: no cover
    logger.info('worker_child_process_spawned')


@worker_process_shutdown.connect
def log_shutdown_process(**kwargs):  # pragma: no cover
    from gamedata.fio.services import close_shared_client

    close_shared_client()
    logger.info('worker_child_process_shutdown')


# task health for the admin: last success/failure and daily counters per task name, never raising into the task
task_prerun.connect(task_health.on_task_prerun, weak=False, dispatch_uid='task_health_prerun')
task_postrun.connect(task_health.on_task_postrun, weak=False, dispatch_uid='task_health_postrun')

app.autodiscover_tasks()
