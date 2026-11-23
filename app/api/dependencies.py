"""FastAPI dependencies shared by all routes.

- Database session factory and Redis client for the request.
- Current account: verify the access token, then load the account and reject
  a disabled account or an insufficient current role.
- ``Idempotency-Key`` header parsing for business commands.
- Rate-limit checks that run before any business transaction.
- Request context: request ID and server time for the response ``meta``.

Design: TECHNICAL_DESIGN.md Sections 7.5, 8.1, 9.3 and 9.5.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

import jwt
from fastapi import Depends, Header, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.middleware import client_ip_of, request_id_of
from app.core import redis_keys
from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.core.rate_limit import RateLimiter
from app.core.security import decode_access_token
from app.domain.enums import Role
from app.models.account import Account
from app.schemas.common import Envelope, ErrorBody, ErrorEnvelope, Meta
from app.schemas.realtime import SessionChange
from app.services.idempotency import CommandOutcome

_bearer = HTTPBearer(auto_error=False)


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_factory(request: Request):
    return request.app.state.session_factory


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def meta(
    request: Request, session_version: int | None = None, result: str | None = None
) -> Meta:
    return Meta(
        request_id=request_id_of(request),
        server_time=datetime.now(UTC),
        session_version=session_version,
        result=result,
    )


async def _account_from_token(request: Request, token: str) -> Account:
    settings: Settings = request.app.state.settings
    _, public_key = settings.require_jwt_keys()
    try:
        claims = decode_access_token(
            token,
            public_key=public_key,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
        account_id = uuid.UUID(claims["sub"])
    except jwt.ExpiredSignatureError as exc:
        raise AppError(ErrorCode.TOKEN_EXPIRED) from exc
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise AppError(ErrorCode.AUTHENTICATION_REQUIRED) from exc

    async with request.app.state.session_factory() as db:
        account = await db.get(Account, account_id)
    if account is None:
        raise AppError(ErrorCode.AUTHENTICATION_REQUIRED)
    if account.disabled_at is not None:
        raise AppError(ErrorCode.ACCOUNT_DISABLED)
    return account


async def get_current_account(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Account:
    if creds is None:
        raise AppError(ErrorCode.AUTHENTICATION_REQUIRED)
    return await _account_from_token(request, creds.credentials)


async def get_optional_account(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Account | None:
    if creds is None:
        return None
    return await _account_from_token(request, creds.credentials)


def require_roles(*roles: Role) -> Callable:
    allowed = {r.value for r in roles}

    async def dep(account: Account = Depends(get_current_account)) -> Account:
        if account.role not in allowed:
            raise AppError(ErrorCode.FORBIDDEN)
        return account

    return dep


require_manager = require_roles(Role.ORGANIZER, Role.ADMIN)
require_admin = require_roles(Role.ADMIN)


def _limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


async def limit_login(request: Request) -> None:
    settings: Settings = request.app.state.settings
    ip = client_ip_of(request) or "unknown"
    await _limiter(request).check(
        request.app.state.redis,
        lambda window: redis_keys.rate_limit_login(ip, window),
        settings.login_limit_per_minute,
    )


async def limit_registration_writes(
    request: Request, account: Account = Depends(get_current_account)
) -> Account:
    settings: Settings = request.app.state.settings
    await _limiter(request).check(
        request.app.state.redis,
        lambda window: redis_keys.rate_limit_write(account.id, window),
        settings.registration_write_limit_per_minute,
    )
    return account


# ---- Business commands: Idempotency-Key, outcome rendering, publication ----


async def idempotency_key(
    key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> uuid.UUID:
    """Required on every authenticated business command (7.5). Declare it after
    the account dependency so an anonymous call is 401 before it is 422."""
    if not key:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Idempotency-Key header is required.",
            details={"header": "Idempotency-Key"},
        )
    try:
        return uuid.UUID(key)
    except ValueError as exc:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Idempotency-Key must be a UUID.",
            details={"header": "Idempotency-Key"},
        ) from exc


def outcome_response(
    request: Request, outcome: CommandOutcome, *, with_result: bool = True
) -> JSONResponse:
    """Render a command outcome, fresh or replayed, with its original status.
    Success carries the result code in ``meta.result`` (participation commands
    only, 8.1; management commands pass ``with_result=False``); a stored
    rejection uses the error envelope and is never retryable."""
    m = meta(
        request,
        session_version=outcome.session_version,
        result=outcome.result_code if with_result and not outcome.is_error else None,
    )
    if outcome.is_error:
        body = ErrorEnvelope(
            error=ErrorBody(
                code=outcome.result_code,
                message=outcome.message or outcome.result_code,
                retryable=False,
                details=outcome.payload,
            ),
            meta=m,
        )
    else:
        body = Envelope[dict](data=outcome.payload, meta=m)
    return JSONResponse(body.model_dump(mode="json"), status_code=outcome.http_status)


async def management_response(request: Request, outcome: CommandOutcome):
    """Publish a management command's changes, then render it without
    ``meta.result``, which only participation commands carry (8.1)."""
    await publish_after_commit(request, outcome.changes)
    return outcome_response(request, outcome, with_result=False)


async def publish_after_commit(request: Request, changes: list[SessionChange]) -> None:
    """Hand one commit's ``SessionChange`` values to the real-time publisher.

    Called by routes only after the service returned, i.e. after commit.
    Publication is best effort and never changes the HTTP result (7.6).
    """
    if not changes:
        return
    # TODO(realtime): Yifeng's app.realtime.publisher is still a stub. Once it
    # exists: await publisher.publish(request.app.state.redis, changes)
