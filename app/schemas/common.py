"""Shared shapes: the success and error envelopes, ``meta``, and the opaque
pagination cursor.

Design: TECHNICAL_DESIGN.md Section 8.1.
"""

import base64
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class Meta(BaseModel):
    request_id: str
    server_time: datetime
    session_version: int | None = None
    result: str | None = None


class Envelope[T](BaseModel):
    data: T
    meta: Meta


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool
    details: dict[str, Any] = {}


class ErrorEnvelope(BaseModel):
    error: ErrorBody
    meta: Meta


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


class StrictModel(BaseModel):

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)



def encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(cursor: str) -> dict[str, Any]:
    from app.core.errors import AppError, ErrorCode

    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded))
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, "Invalid cursor.") from exc
    if not isinstance(value, dict):
        raise AppError(ErrorCode.VALIDATION_ERROR, "Invalid cursor.")
    return value
