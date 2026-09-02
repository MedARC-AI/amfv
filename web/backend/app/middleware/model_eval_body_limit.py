"""Bound request bodies for model-label correction submissions."""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = ["MAX_MODEL_EVAL_REQUEST_BYTES", "ModelEvalBodyLimitMiddleware"]

MAX_MODEL_EVAL_REQUEST_BYTES = 4 * 1024 * 1024


class _RequestBodyTooLarge(Exception):
    """Signal that the request stream crossed its endpoint-specific limit."""


class ModelEvalBodyLimitMiddleware:
    """Reject oversized correction JSON before FastAPI parses the full body."""

    def __init__(self, app: ASGIApp, *, api_prefix: str) -> None:
        self.app = app
        self.path_prefix = f"{api_prefix}/review/fact-decomp/"
        self.path_suffix = "/model-eval"

    def _applies(self, scope: Scope) -> bool:
        path = str(scope.get("path", ""))
        return (
            scope.get("type") == "http"
            and scope.get("method") == "POST"
            and path.startswith(self.path_prefix)
            and path.endswith(self.path_suffix)
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._applies(scope):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_MODEL_EVAL_REQUEST_BYTES:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                pass

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_MODEL_EVAL_REQUEST_BYTES:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            {"detail": "Model-evaluation request body is too large"}, status_code=413
        )
        await response(scope, receive, send)
