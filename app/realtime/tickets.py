"""WebSocket connection tickets: issue (API side) and atomic single-use
consume (gateway side).

Design: TECHNICAL_DESIGN.md Section 9.4.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from redis.exceptions import RedisError

from app.core.constants import WS_TICKET_TTL_SECONDS
from app.core.metrics import REDIS_OPERATION_FAILURES
from app.core.redis_keys import websocket_ticket
from app.schemas.realtime import TicketResponse, TicketScope

_ISSUE_ATTEMPTS = 3


class TicketRedis(Protocol):
    async def set(
        self, name: str, value: str, *, ex: int, nx: bool
    ) -> bool | None: ...

    async def getdel(self, name: str) -> str | None: ...


class TicketGrant(BaseModel):
    """The private ticket value stored briefly in Redis."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: uuid.UUID | None
    access_expires_at: datetime | None
    scopes: tuple[TicketScope, ...]

    @model_validator(mode="after")
    def validate_identity_and_scopes(self) -> TicketGrant:
        scope_set = set(self.scopes)
        if TicketScope.PUBLIC not in scope_set:
            raise ValueError("every ticket requires public scope")
        if self.account_id is None:
            if self.access_expires_at is not None or scope_set != {TicketScope.PUBLIC}:
                raise ValueError("anonymous tickets may have only public scope")
        elif (
            self.access_expires_at is None
            or self.access_expires_at.utcoffset() is None
            or TicketScope.ACCOUNT not in scope_set
        ):
            raise ValueError(
                "authenticated tickets require expiry and account scope"
            )
        return self


class TicketError(Exception):
    """A ticket could not be issued or accepted."""


def _digest(ticket: str) -> str:
    return hashlib.sha256(ticket.encode()).hexdigest()


async def issue_ticket(
    redis: TicketRedis,
    *,
    account_id: uuid.UUID | None,
    access_expires_at: datetime | None,
    scopes: tuple[TicketScope, ...],
) -> TicketResponse:
    """Store a random ticket digest and return the secret exactly once."""
    grant = TicketGrant(
        account_id=account_id,
        access_expires_at=access_expires_at,
        scopes=scopes,
    )
    value = grant.model_dump_json()

    for _ in range(_ISSUE_ATTEMPTS):
        ticket = secrets.token_urlsafe(32)
        try:
            created = await redis.set(
                websocket_ticket(_digest(ticket)),
                value,
                ex=WS_TICKET_TTL_SECONDS,
                nx=True,
            )
        except RedisError:
            REDIS_OPERATION_FAILURES.labels("ticket_issue").inc()
            raise
        if created:
            return TicketResponse(ticket=ticket, scopes=scopes)

    raise TicketError("could not allocate a unique WebSocket ticket")


async def consume_ticket(
    redis: TicketRedis, ticket: str, *, now: datetime | None = None
) -> TicketGrant:
    """Atomically read and delete a ticket across all gateway replicas."""
    try:
        raw = await redis.getdel(websocket_ticket(_digest(ticket)))
    except RedisError:
        REDIS_OPERATION_FAILURES.labels("ticket_consume").inc()
        raise
    if raw is None:
        raise TicketError("ticket is missing, expired, or already used")

    try:
        grant = TicketGrant.model_validate_json(raw)
    except ValidationError as exc:
        raise TicketError("ticket payload is invalid") from exc

    current_time = now or datetime.now(UTC)
    if (
        grant.access_expires_at is not None
        and grant.access_expires_at <= current_time
    ):
        raise TicketError("authentication expired before connection")
    return grant
