import asyncio
import runpy
from pathlib import Path

from core.workers import FinishOnShutdown, HttpOnlyUvicornWorker
from django.core.asgi import get_asgi_application

GUNICORN_CONF = Path(__file__).resolve().parents[2] / 'gunicorn.conf.py'


def test_worker_disables_websockets() -> None:
    assert HttpOnlyUvicornWorker.CONFIG_KWARGS['ws'] == 'none'


def test_gunicorn_uses_http_only_worker() -> None:
    conf = runpy.run_path(str(GUNICORN_CONF))
    assert conf['worker_class'] == 'core.workers.HttpOnlyUvicornWorker'


def _request_during_shutdown(app, path: str) -> list[dict]:
    """One GET whose receive() answers http.disconnect after the body, as uvicorn's does once its worker shuts down."""
    messages = iter([{'type': 'http.request', 'body': b'', 'more_body': False}])
    sent: list[dict] = []

    async def receive() -> dict:
        return next(messages, {'type': 'http.disconnect'})

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'GET', 'scheme': 'http',
        'path': path, 'raw_path': path.encode(), 'query_string': b'', 'root_path': '',
        'headers': [(b'host', b'testserver')], 'client': ('127.0.0.1', 1), 'server': ('testserver', 80),
    }  # fmt: skip
    asyncio.run(asyncio.wait_for(app(scope, receive, send), timeout=5))
    return sent


def test_a_request_in_flight_at_shutdown_still_gets_its_response() -> None:
    django_app = get_asgi_application()
    # Django alone aborts the view: no response, so uvicorn sends a 500
    assert _request_during_shutdown(django_app, '/no-such-page/') == []

    sent = _request_during_shutdown(FinishOnShutdown(django_app, frozenset({'/data/stream/'})), '/no-such-page/')
    assert [m['status'] for m in sent if m['type'] == 'http.response.start'] == [404]


def test_a_stream_still_hears_the_shutdown() -> None:
    heard: list[str] = []

    async def stream_app(scope, receive, send) -> None:
        await receive()
        heard.append((await receive())['type'])

    _request_during_shutdown(FinishOnShutdown(stream_app, frozenset({'/data/stream/'})), '/data/stream/')
    assert heard == ['http.disconnect']


def test_the_live_stream_is_exempt() -> None:
    from core.asgi import application

    assert application.stream_paths == frozenset({'/data/stream/'})
