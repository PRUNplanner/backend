import os

# basic
bind = '0.0.0.0:8000'
pythonpath = 'backend'
wsgi_app = 'core.wsgi:application'

# worker
workers = int(os.getenv('GUNICORN_WORKERS', 3))
threads = int(os.getenv('GUNICORN_THREADS', 2))
worker_class = 'gthread'
preload_app = True

# restart logic
max_requests = 1000
max_requests_jitter = 100

# logging
accesslog = '-'
errorlog = '-'
loglevel = 'info'

# timeout
timeout = 30
