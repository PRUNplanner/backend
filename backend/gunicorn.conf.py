import os

# basic
bind = '0.0.0.0:8000'
pythonpath = 'backend'
wsgi_app = 'core.asgi:application'

# worker
workers = int(os.getenv('GUNICORN_WORKERS', 3))
# threads = int(os.getenv('GUNICORN_THREADS', 2)) # not required on UvicornWorker
worker_class = 'uvicorn.workers.UvicornWorker'
preload_app = True

# restart logic
max_requests = 2000
max_requests_jitter = 200

# logging
accesslog = '-'
errorlog = '-'
loglevel = 'info'

# timeout
timeout = 30
keepalive = 5
