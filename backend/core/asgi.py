"""
ASGI config for core project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application
from django.urls import reverse

from core.workers import FinishOnShutdown

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.config.django.local')

application = FinishOnShutdown(get_asgi_application(), stream_paths=frozenset({reverse('data:data-stream')}))
