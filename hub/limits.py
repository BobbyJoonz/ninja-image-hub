"""Bounded request bodies.

Control-plane JSON stays at 64 KiB. Reference-image uploads are the one large-body
route, so they get their own cap (derived from the live upload settings) and are streamed
straight to the ASGI app instead of being buffered in memory by this middleware.

The upload route itself re-checks every file and the total before touching the disk, so a
missing/forged Content-Length cannot exceed the documented limits; this middleware is the
first cheap gate (413) for honest browsers.
"""

from starlette.responses import JSONResponse


class RequestBodyLimit:
    def __init__(
        self,
        app,
        max_bytes=64 * 1024,
        upload_paths=(),
        upload_limit=None,
    ):
        self.app = app
        self.max_bytes = max_bytes
        self.upload_paths = set(upload_paths or ())
        self.upload_limit = upload_limit

    def _upload_cap(self):
        if callable(self.upload_limit):
            try:
                return int(self.upload_limit())
            except Exception:
                return 0
        return int(self.upload_limit or 0)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in (
            "POST",
            "PATCH",
            "PUT",
        ):
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        if path in self.upload_paths:
            cap = self._upload_cap()
            headers = dict(scope.get("headers", []))
            if cap > 0:
                raw_length = headers.get(b"content-length")
                if raw_length is None:
                    # A chunked upload would otherwise stream unbounded into Starlette's
                    # spool before the route can enforce its total. Browsers and curl -F
                    # always send Content-Length for multipart bodies.
                    return await JSONResponse(
                        {
                            "detail": "Uploads require a Content-Length header; chunked uploads are not accepted."
                        },
                        status_code=411,
                    )(scope, receive, send)
                try:
                    declared = int(raw_length)
                except ValueError:
                    return await JSONResponse(
                        {"detail": "Invalid content length."}, status_code=400
                    )(scope, receive, send)
                if declared > cap:
                    return await JSONResponse(
                        {
                            "detail": f"Upload exceeds the {cap // (1024 * 1024)} MiB request limit."
                        },
                        status_code=413,
                    )(scope, receive, send)
            # Stream: Starlette spools each part to disk; the route enforces the real
            # per-file and total limits before storing anything.
            return await self.app(scope, receive, send)

        async def reject():
            await JSONResponse(
                {"detail": "JSON request exceeds 64 KiB."}, status_code=413
            )(scope, receive, send)

        headers = dict(scope.get("headers", []))
        try:
            if int(headers.get(b"content-length", b"0")) > self.max_bytes:
                return await reject()
        except ValueError:
            return await JSONResponse(
                {"detail": "Invalid content length."}, status_code=400
            )(scope, receive, send)
        chunks = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > self.max_bytes:
                return await reject()
            chunks.append(body)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {
                "type": "http.request",
                "body": b"".join(chunks),
                "more_body": False,
            }

        await self.app(scope, replay, send)
