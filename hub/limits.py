"""Bounded JSON control-plane bodies; large image bytes travel on file routes."""

from starlette.responses import JSONResponse


class RequestBodyLimit:
    def __init__(self, app, max_bytes=64 * 1024):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in (
            "POST",
            "PATCH",
            "PUT",
        ):
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
