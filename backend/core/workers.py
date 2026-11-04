import asyncio
from collections.abc import Awaitable, Callable

from uvicorn._types import ASGIReceiveCallable, ASGIReceiveEvent, ASGISendCallable, Scope
from uvicorn.workers import UvicornWorker


class HttpOnlyUvicornWorker(UvicornWorker):
    """UvicornWorker without websocket support.

    The app serves no websockets (live updates are SSE), so upgrade requests
    from scanners get a plain HTTP response from uvicorn instead of reaching
    Django, which raises on websocket scopes.
    """

    CONFIG_KWARGS = {**UvicornWorker.CONFIG_KWARGS, 'ws': 'none'}


class FinishOnShutdown:
    """ASGI wrapper: a request in flight when its worker shuts down still gets its response.

    On a graceful shutdown (the max_requests recycle, a deploy) uvicorn answers every open request's
    receive() with http.disconnect. Django then aborts the view and uvicorn sends a 500 instead. Past the
    body, this wrapper never reports a disconnect, so the view finishes, as it did under WSGI; a client that
    really left gets an answer nobody reads. Streams keep the disconnect: it is what ends them on shutdown.
    """

    def __init__(self, app: Callable[..., Awaitable[None]], stream_paths: frozenset[str]) -> None:
        self.app = app
        self.stream_paths = stream_paths

    async def __call__(self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
        if scope['type'] != 'http' or scope['path'] in self.stream_paths:
            return await self.app(scope, receive, send)

        body_read = False

        async def receive_body() -> ASGIReceiveEvent:
            nonlocal body_read
            if body_read:
                # only Django's disconnect listener asks now; it is cancelled once the response is sent
                await asyncio.Future()
            message = await receive()
            if message['type'] == 'http.request' and not message.get('more_body', False):
                body_read = True
            return message

        return await self.app(scope, receive_body, send)
