"""Attendee command and query routes: book, join the waitlist, confirm, cancel,
decline, leave, ``my-participation``, ``/me/participations`` and
``GET /requests/{request_id}``.

Decline and leave are Jianye's and are added here with the same shape as
cancel.

Design: TECHNICAL_DESIGN.md Section 8.4.
"""

import uuid

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import (
    get_current_account,
    get_factory,
    get_settings_dep,
    idempotency_key,
    limit_registration_writes,
    meta,
    outcome_response,
    publish_after_commit,
)
from app.core.constants import EVENT_LIST_PAGE_SIZE
from app.core.errors import AppError, ErrorCode
from app.models.account import Account
from app.schemas.common import Envelope, ErrorEnvelope, Page
from app.schemas.participations import ConfirmRequest, EmptyBody, ParticipationView
from app.services import booking
from app.services import participations as svc
from app.services.idempotency import CommandOutcome, find_receipt, replay

router = APIRouter(tags=["participations"])

# The status code in each decorator only documents the usual success; the
# actual one (201, 200 or a stored 409) comes from the outcome.
COMMAND = {
    "response_model": Envelope[ParticipationView],
    "responses": {404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
}


async def _respond(request: Request, outcome: CommandOutcome):
    # A replay publishes nothing: its changes went out with the original commit.
    await publish_after_commit(request, outcome.changes)
    return outcome_response(request, outcome)


# Dependency order matters: the rate limiter authenticates first (401), then
# counts the write (429, no receipt), then the key is parsed (422).


@router.post("/sessions/{session_id}/book", status_code=201, **COMMAND)
async def book(
    request: Request,
    session_id: uuid.UUID,
    body: EmptyBody | None = None,
    account: Account = Depends(limit_registration_writes),
    key: uuid.UUID = Depends(idempotency_key),
    factory=Depends(get_factory),
    settings=Depends(get_settings_dep),
):
    outcome = await booking.book(
        factory, settings, account=account, session_id=session_id, request_id=key
    )
    return await _respond(request, outcome)


@router.post("/sessions/{session_id}/waitlist", status_code=201, **COMMAND)
async def join_waitlist(
    request: Request,
    session_id: uuid.UUID,
    body: EmptyBody | None = None,
    account: Account = Depends(limit_registration_writes),
    key: uuid.UUID = Depends(idempotency_key),
    factory=Depends(get_factory),
    settings=Depends(get_settings_dep),
):
    outcome = await booking.join_waitlist(
        factory, settings, account=account, session_id=session_id, request_id=key
    )
    return await _respond(request, outcome)


@router.post("/participations/{participation_id}/confirm", **COMMAND)
async def confirm(
    request: Request,
    participation_id: uuid.UUID,
    body: ConfirmRequest,
    account: Account = Depends(limit_registration_writes),
    key: uuid.UUID = Depends(idempotency_key),
    factory=Depends(get_factory),
    settings=Depends(get_settings_dep),
):
    outcome = await svc.confirm(
        factory,
        settings,
        account=account,
        participation_id=participation_id,
        simulation_result=body.simulation_result,
        request_id=key,
    )
    return await _respond(request, outcome)


@router.post("/participations/{participation_id}/cancel", **COMMAND)
async def cancel(
    request: Request,
    participation_id: uuid.UUID,
    body: EmptyBody | None = None,
    account: Account = Depends(limit_registration_writes),
    key: uuid.UUID = Depends(idempotency_key),
    factory=Depends(get_factory),
    settings=Depends(get_settings_dep),
):
    outcome = await svc.cancel(
        factory, settings, account=account, participation_id=participation_id, request_id=key
    )
    return await _respond(request, outcome)


@router.get(
    "/sessions/{session_id}/my-participation", response_model=Envelope[ParticipationView]
)
async def my_participation(
    request: Request,
    session_id: uuid.UUID,
    account: Account = Depends(get_current_account),
    factory=Depends(get_factory),
):
    view = await svc.my_participation(factory, account, session_id)
    return Envelope(data=view, meta=meta(request, session_version=view.version))


@router.get("/me/participations", response_model=Envelope[Page[ParticipationView]])
async def my_participations(
    request: Request,
    cursor: str | None = None,
    limit: int = Query(EVENT_LIST_PAGE_SIZE, ge=1, le=100),
    account: Account = Depends(get_current_account),
    factory=Depends(get_factory),
):
    # Several sessions: each item carries its own version, meta has none (8.1).
    page = await svc.my_participations(factory, account, cursor=cursor, limit=limit)
    return Envelope(data=page, meta=meta(request))


@router.get("/participations/{participation_id}", response_model=Envelope[ParticipationView])
async def get_participation(
    request: Request,
    participation_id: uuid.UUID,
    account: Account = Depends(get_current_account),
    factory=Depends(get_factory),
):
    view = await svc.get_participation(factory, account, participation_id)
    return Envelope(data=view, meta=meta(request, session_version=view.version))


@router.get(
    "/requests/{request_id}",
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)
async def get_request_result(
    request: Request,
    request_id: uuid.UUID,
    account: Account = Depends(get_current_account),
    factory=Depends(get_factory),
):
    """The stored outcome of the caller's command, exactly as first produced.
    404 if it never committed, failed without a receipt, or is someone else's."""
    async with factory() as db:
        r = await find_receipt(db, account.id, request_id)
    if r is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND)
    return outcome_response(request, replay(r, r.operation, r.parameter_hash))
