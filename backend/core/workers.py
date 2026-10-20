from uvicorn.workers import UvicornWorker


class HttpOnlyUvicornWorker(UvicornWorker):
    """UvicornWorker without websocket support.

    The app serves no websockets (live updates are SSE), so upgrade requests
    from scanners get a plain HTTP response from uvicorn instead of reaching
    Django, which raises on websocket scopes.
    """

    CONFIG_KWARGS = {**UvicornWorker.CONFIG_KWARGS, 'ws': 'none'}
