"""``POST /realtime/tickets``: issues a single-use WebSocket connection ticket.

The WebSocket endpoint itself is served by the gateway process in
``app.realtime.gateway``, not by this API.

Design: TECHNICAL_DESIGN.md Section 9.4.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from redis.exceptions import RedisError

from app.api.dependencies import (
    AccessContext,
    get_optional_access_context,
    meta,
)
from app.core.errors import AppError, ErrorCode
from app.domain.enums import Role
from app.realtime.tickets import issue_ticket
from app.schemas.common import Envelope
from app.schemas.realtime import TicketResponse, TicketScope

router = APIRouter(tags=["realtime"])


@router.post(
    "/realtime/tickets",
    response_model=Envelope[TicketResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_ticket(
    request: Request,
    access: Annotated[
        AccessContext | None, Depends(get_optional_access_context)
    ],
) -> Envelope[TicketResponse]:
    scopes = [TicketScope.PUBLIC]
    if access is not None:
        scopes.append(TicketScope.ACCOUNT)
        if access.account.role in {Role.ORGANIZER, Role.ADMIN}:
            scopes.append(TicketScope.MANAGEMENT)

    try:
        ticket = await issue_ticket(
            request.app.state.redis,
            account_id=None if access is None else access.account.id,
            access_expires_at=None if access is None else access.expires_at,
            scopes=tuple(scopes),
        )
    except RedisError as exc:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE) from exc
    return Envelope(data=ticket, meta=meta(request))
