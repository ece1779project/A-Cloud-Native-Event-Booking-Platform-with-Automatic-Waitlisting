"""ASGI middleware: ``X-Request-ID`` handling, structured access log, HTTP
metrics and trusted-proxy client address resolution.

Design: TECHNICAL_DESIGN.md Sections 8.1, 9.5 and 14.
"""

import ipaddress
import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _SAFE_ID.match(incoming) else str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


def request_id_of(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid.uuid4())


type _Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def normalize_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        addr = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return str(addr)


def _trusted(ip: str, proxies: tuple[_Network, ...]) -> bool:
    addr = ipaddress.ip_address(ip)
    return any(addr in net for net in proxies)


def resolve_client_ip(
    peer: str | None, forwarded_for: str | None, proxies: tuple[_Network, ...]
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
