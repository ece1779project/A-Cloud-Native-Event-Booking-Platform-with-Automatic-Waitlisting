"""Structured JSON logging, the request/correlation ID context and redaction
of secrets and personal data.

Design: TECHNICAL_DESIGN.md Sections 9.5 and 14.
"""

import json
import logging
import re
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "csrf_token",
    "jwt_private_key",
    "password",
    "refresh_token",
    "sec_websocket_protocol",
    "token",
}
_SAFE_EXTRA_FIELDS = {
    "correlation_id",
    "duration",
    "error_code",
    "error_type",
    "message_type",
    "method",
    "operation",
    "outcome",
    "scan",
    "session_id",
    "status",
}
_TEXT_REDACTIONS = (
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
    (
        re.compile(r"seatflow\.ticket\.[A-Za-z0-9_-]+"),
        "seatflow.ticket.[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)\b(password|refresh_token|access_token|csrf_token)"
            r"=([^\s&,;]+)"
        ),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)\b(authorization|cookie|sec-websocket-protocol):[^\r\n]*"
        ),
        r"\1: [REDACTED]",
    ),
)


def configure_logging(*, service: str, environment: str, level: str) -> None:
    """Configure one JSON stream handler for an application process."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service=service, environment=environment))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Uvicorn installs its own text handlers before application startup.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True


def bind_request_id(value: str) -> Token[str | None]:
    return _request_id.set(value)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


class JsonFormatter(logging.Formatter):
    def __init__(self, *, service: str, environment: str) -> None:
        super().__init__()
        self.service = service
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "service": self.service,
            "environment": self.environment,
            "logger": record.name,
            "message": _redact_text(record.getMessage()),
        }
        request_id = _request_id.get()
        if request_id is not None:
            payload["request_id"] = request_id

        for field in _SAFE_EXTRA_FIELDS:
            if hasattr(record, field):
                payload[field] = _redact_value(field, getattr(record, field))
        if record.exc_info:
            payload["exception"] = _redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, separators=(",", ":"), default=str)


def _redact_value(key: str, value: Any) -> Any:
    normalized = key.lower().replace("-", "_")
    if normalized in _SENSITIVE_KEYS:
        return _REDACTED
    if isinstance(value, dict):
        return {
            item_key: _redact_value(item_key, item)
            for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_value(key, item) for item in value]
    return _redact_text(value) if isinstance(value, str) else value


def _redact_text(value: str) -> str:
    for pattern, replacement in _TEXT_REDACTIONS:
        value = pattern.sub(replacement, value)
    return value
