"""Request IDs, access metrics/logs, and trusted-proxy client IP resolution.

Design: TECHNICAL_DESIGN.md Sections 8.1, 9.5 and 14.
"""

import ipaddress
import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import bind_request_id, reset_request_id
from app.core.metrics import HTTP_REQUEST_DURATION, HTTP_REQUESTS

log = logging.getLogger(__name__)
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class HttpObservabilityMiddleware:
    """Add a safe correlation ID, access log, and bounded HTTP metrics."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.monotonic()
        request_id = _request_id_from_scope(scope)
        scope.setdefault("state", {})["request_id"] = request_id
        request_id_token = bind_request_id(request_id)
        status_code: int | None = None

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            status_code = status_code or 500
            raise
        finally:
            route = getattr(scope.get("route"), "path", "<unmatched>")
            status = str(status_code or 500)
            duration = time.monotonic() - started
            HTTP_REQUESTS.labels(scope["method"], route, status).inc()
            HTTP_REQUEST_DURATION.labels(scope["method"], route).observe(duration)
            log.info(
                "http request completed",
                extra={
                    "request_id": request_id,
                    "operation": route,
                    "method": scope["method"],
                    "status": status,
                    "duration": duration,
                },
            )
            reset_request_id(request_id_token)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Compatibility middleware for isolated tests and small ASGI apps."""

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request_id = (
            incoming if _SAFE_ID.fullmatch(incoming) else str(uuid.uuid4())
        )
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


def _request_id_from_scope(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name.lower() != b"x-request-id":
            continue
        try:
            candidate = value.decode("ascii")
        except UnicodeDecodeError:
            break
        if _SAFE_ID.fullmatch(candidate):
            return candidate
        break
    return str(uuid.uuid4())


def request_id_of(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid.uuid4())


type _Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def normalize_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if (
        isinstance(address, ipaddress.IPv6Address)
        and address.ipv4_mapped is not None
    ):
        address = address.ipv4_mapped
    return str(address)


def _trusted(ip: str, proxies: tuple[_Network, ...]) -> bool:
    address = ipaddress.ip_address(ip)
    return any(address in network for network in proxies)


def resolve_client_ip(
    peer: str | None,
    forwarded_for: str | None,
    proxies: tuple[_Network, ...],
) -> str | None:
    client = normalize_ip(peer)
    if client is None or not proxies or not _trusted(client, proxies):
        return client
    for hop in reversed((forwarded_for or "").split(",")):
        hop_ip = normalize_ip(hop)
        if hop_ip is None:
            break
        client = hop_ip
        if not _trusted(hop_ip, proxies):
            break
    return client


def client_ip_of(request: Request) -> str | None:
    settings = request.app.state.settings
    return resolve_client_ip(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for"),
        settings.trusted_proxies,
    )
