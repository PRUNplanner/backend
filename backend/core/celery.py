import logging.config

from celery import Celery
from celery.signals import setup_logging
from django_structlog.celery.steps import DjangoStructLogInitStep

app = Celery('prunplanner')


app.steps['worker'].add(DjangoStructLogInitStep)  # ty:ignore[not-subscriptable]

app.config_from_object('django.conf:settings', namespace='CELERY')


@setup_logging.connect
def config_loggers(*args, **kwargs):
    from django.conf import settings

    logging.config.dictConfig(settings.LOGGING)


app.autodiscover_tasks()
