import runpy
from pathlib import Path

from core.workers import HttpOnlyUvicornWorker

GUNICORN_CONF = Path(__file__).resolve().parents[2] / 'gunicorn.conf.py'


def test_worker_disables_websockets() -> None:
    assert HttpOnlyUvicornWorker.CONFIG_KWARGS['ws'] == 'none'


def test_gunicorn_uses_http_only_worker() -> None:
    conf = runpy.run_path(str(GUNICORN_CONF))
    assert conf['worker_class'] == 'core.workers.HttpOnlyUvicornWorker'
