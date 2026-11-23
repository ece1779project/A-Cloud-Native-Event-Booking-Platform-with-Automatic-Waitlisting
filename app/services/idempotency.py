"""Command receipts: the parameter hash, the fast-path and under-lock receipt
lookups, storing the receipt with the business effects, replay, and the
losing-transaction retry.

A command's business function returns a ``CommandOutcome``. Returning one,
including a business rejection such as ``WAITLIST_REQUIRED``, commits the
transaction with its receipt. Raising ``AppError`` (not found, forbidden,
lock timeout) rolls everything back and leaves the key unused.

Design: TECHNICAL_DESIGN.md Section 7.5; DATABASE_DESIGN.md Section 4.
"""

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError, ErrorCode
from app.db.engine import UNIQUE_VIOLATION, db_now, sqlstate
from app.models.request_result import RequestResult
from app.schemas.realtime import SessionChange


@dataclass(slots=True)
class CommandOutcome:
    """What a command produced: the HTTP status, the result or error code and
    the body. ``payload`` is ``data`` on success and ``error.details`` on a
    rejection. ``changes`` are published after commit and never stored."""

    http_status: int
    result_code: str
    payload: dict[str, Any]
    session_version: int | None = None
    participation_id: uuid.UUID | None = None
    message: str | None = None
    changes: list[SessionChange] = field(default_factory=list)
    replayed: bool = False

    @property
    def is_error(self) -> bool:
        return self.http_status >= 400

    @classmethod
    def reject(
        cls,
        code: ErrorCode,
        *,
        details: dict[str, Any] | None = None,
        session_version: int | None = None,
        participation_id: uuid.UUID | None = None,
        changes: list[SessionChange] | None = None,
    ) -> CommandOutcome:
        """A business rejection: committed and stored, unlike ``AppError``."""
        err = AppError(code)
        return cls(
            http_status=err.status,
            result_code=code.value,
            payload=details or {},
            session_version=session_version,
            participation_id=participation_id,
            message=err.message,
            changes=changes or [],
        )


def parameter_hash(operation: str, **params: Any) -> bytes:
    """SHA-256 of the canonical JSON of the operation and its inputs: sorted
    keys, compact separators, UUIDs and datetimes as strings."""
    canonical = json.dumps(
        {"op": operation, **params}, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode()).digest()


async def find_receipt(
    db: AsyncSession, account_id: uuid.UUID, request_id: uuid.UUID
) -> RequestResult | None:
    return await db.scalar(
        select(RequestResult)
        .where(
            RequestResult.account_id == account_id,
            RequestResult.request_id == request_id,
        )
        .execution_options(populate_existing=True)
    )


def replay(receipt: RequestResult, operation: str, phash: bytes) -> CommandOutcome:
    """The stored outcome, or ``409 IDEMPOTENCY_CONFLICT`` for different inputs."""
    if receipt.operation != operation or receipt.parameter_hash != phash:
        raise AppError(ErrorCode.IDEMPOTENCY_CONFLICT)
    body = receipt.result_body
    return CommandOutcome(
        http_status=receipt.http_status,
        result_code=receipt.result_code,
        payload=body["payload"],
        session_version=body.get("session_version"),
        participation_id=receipt.participation_id,
        message=body.get("message"),
        replayed=True,
    )


async def store_receipt(
    db: AsyncSession,
    *,
    account_id: uuid.UUID,
    request_id: uuid.UUID,
    operation: str,
    phash: bytes,
    outcome: CommandOutcome,
    now: datetime,
) -> None:
    db.add(
        RequestResult(
            account_id=account_id,
            request_id=request_id,
            operation=operation,
            parameter_hash=phash,
            participation_id=outcome.participation_id,
            http_status=outcome.http_status,
            result_code=outcome.result_code,
            result_body={
                "payload": outcome.payload,
                "session_version": outcome.session_version,
                "message": outcome.message,
            },
            recorded_at=now,
        )
    )
    # Flush now so a duplicate key raises here, inside run_command's handler.
    await db.flush()


@dataclass(slots=True)
class CommandContext:
    account_id: uuid.UUID
    request_id: uuid.UUID
    operation: str
    phash: bytes

    async def recheck(self, db: AsyncSession) -> CommandOutcome | None:
        """Call right after taking the locks: if the original request
        committed while this one waited, return its replay."""
        r = await find_receipt(db, self.account_id, self.request_id)
        return replay(r, self.operation, self.phash) if r else None


Execute = Callable[[AsyncSession, CommandContext], Awaitable[CommandOutcome]]


async def run_command(
    factory: async_sessionmaker[AsyncSession],
    *,
    account_id: uuid.UUID,
    request_id: uuid.UUID,
    operation: str,
    phash: bytes,
    execute: Execute,
) -> CommandOutcome:
    """Fast-path lookup, then ``execute`` and the receipt in one transaction.
    A duplicate-key loser rolls back completely and replays the winner from a
    fresh transaction."""
    async with factory() as db:
        r = await find_receipt(db, account_id, request_id)
    if r is not None:
        return replay(r, operation, phash)

    ctx = CommandContext(account_id, request_id, operation, phash)
    try:
        async with factory.begin() as db:
            outcome = await execute(db, ctx)
            if not outcome.replayed:
                await store_receipt(
                    db,
                    account_id=account_id,
                    request_id=request_id,
                    operation=operation,
                    phash=phash,
                    outcome=outcome,
                    now=await db_now(db),
                )
        return outcome
    except IntegrityError as exc:
        if sqlstate(exc) != UNIQUE_VIOLATION:
            raise
        async with factory() as db:
            r = await find_receipt(db, account_id, request_id)
        if r is None:
            raise  # some other unique violation: a bug, so a 500
        return replay(r, operation, phash)
