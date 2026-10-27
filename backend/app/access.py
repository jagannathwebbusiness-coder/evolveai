"""Optional shared-password gate for hosted deployments (off unless ACCESS_PASSWORD is set)."""

import hashlib
import hmac
import json

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

COOKIE = "evolve_session"
OPEN_PATHS = {"/health", "/auth/login", "/auth/logout", "/auth/status"}


def session_value(password: str) -> str:
    return hmac.new(password.encode(), b"evolve-session-v1", hashlib.sha256).hexdigest()


def is_authorized(request: Request, password: str) -> bool:
    supplied = request.cookies.get(COOKIE, "")
    return hmac.compare_digest(supplied, session_value(password))


class AccessGate:
    def __init__(self, app: ASGIApp, password: str):
        self.app = app
        self.password = password

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if (
            scope["type"] != "http"
            or not self.password
            or scope["method"] == "OPTIONS"
            or scope["path"] in OPEN_PATHS
            or is_authorized(Request(scope), self.password)
        ):
            return await self.app(scope, receive, send)
        body = json.dumps(
            {
                "detail": {
                    "code": "auth_required",
                    "message": "Enter the access password to use this deployment.",
                }
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
