"""Maps ``app.core.errors`` exceptions, validation errors and unexpected
failures to the error envelope and HTTP status.

Expected business rejections are not logged as server errors.

Design: TECHNICAL_DESIGN.md Sections 8.1 and 8.5.
"""

import logging
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.middleware import request_id_of
from app.core.errors import AppError, ErrorCode
from app.db.engine import is_retryable_timeout
from app.schemas.common import ErrorBody, ErrorEnvelope, Meta

log = logging.getLogger(__name__)


def error_response(
    request: Request,
    code: ErrorCode,
    message: str,
    *,
    retryable: bool,
    status: int,
    details: dict | None = None,
    session_version: int | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorEnvelope(
        error=ErrorBody(
            code=code, message=message, retryable=retryable, details=details or {}
        ),
        meta=Meta(
            request_id=request_id_of(request),
            server_time=datetime.now(UTC),
            session_version=session_version,
        ),
    )
    return JSONResponse(
        body.model_dump(mode="json"), status_code=status, headers=headers
    )


def _from_app_error(request: Request, exc: AppError) -> JSONResponse:
    return error_response(
        request,
        exc.code,
        exc.message,
        retryable=exc.retryable,
        status=exc.status,
        details=exc.details,
        session_version=exc.session_version,
        headers=exc.headers,
    )


def _simple(request: Request, code: ErrorCode, **kw) -> JSONResponse:
    return _from_app_error(request, AppError(code, **kw))


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        return _from_app_error(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError):
        errors = [
            {"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]}
            for e in exc.errors()
        ]
        return _simple(request, ErrorCode.VALIDATION_ERROR, details={"errors": errors})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = {
            401: ErrorCode.AUTHENTICATION_REQUIRED,
            403: ErrorCode.FORBIDDEN,
            404: ErrorCode.RESOURCE_NOT_FOUND,
            405: ErrorCode.RESOURCE_NOT_FOUND,
        }.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        return _simple(request, code)

    @app.exception_handler(DBAPIError)
    async def db_error(request: Request, exc: DBAPIError):
        if is_retryable_timeout(exc):
            return _simple(request, ErrorCode.LOCK_TIMEOUT)
        if (
            isinstance(exc, (OperationalError, InterfaceError))
            or exc.connection_invalidated
        ):
            return _simple(request, ErrorCode.DATABASE_UNAVAILABLE)
        log.exception("unexpected database error")
        return _simple(request, ErrorCode.INTERNAL_ERROR)

    @app.exception_handler(OSError)
    async def os_error(request: Request, exc: OSError):
        return _simple(request, ErrorCode.DATABASE_UNAVAILABLE)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception("unhandled error")
        return _simple(request, ErrorCode.INTERNAL_ERROR)
