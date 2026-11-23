"""Event-session routes: public detail with availability, and organizer edit,
cancel and roster.

Design: TECHNICAL_DESIGN.md Section 8.3.
"""

import uuid

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import (
    get_factory,
    get_optional_account,
    get_settings_dep,
    idempotency_key,
    management_response,
    meta,
    require_manager,
)
from app.models.account import Account
from app.schemas.common import Envelope
from app.schemas.sessions import CancelRequest, RosterView, SessionPatch, SessionView
from app.services import sessions as svc

router = APIRouter(tags=["sessions"])


@router.get("/sessions/{session_id}", response_model=Envelope[SessionView])
async def get_session(
    request: Request,
    session_id: uuid.UUID,
    viewer: Account | None = Depends(get_optional_account),
    factory=Depends(get_factory),
):
    view = await svc.get_session(factory, viewer, session_id)
    return Envelope(data=view, meta=meta(request, session_version=view.version))


@router.patch("/sessions/{session_id}", response_model=Envelope[SessionView])
async def patch_session(
    request: Request,
    session_id: uuid.UUID,
    body: SessionPatch,
    actor: Account = Depends(require_manager),
    key: uuid.UUID = Depends(idempotency_key),
    factory=Depends(get_factory),
    settings=Depends(get_settings_dep),
):
    outcome = await svc.patch_session(
        factory, settings, actor, session_id, body, request_id=key
    )
    return await management_response(request, outcome)


@router.post("/sessions/{session_id}/cancel", response_model=Envelope[SessionView])
async def cancel_session(
    request: Request,
    session_id: uuid.UUID,
    body: CancelRequest,
    actor: Account = Depends(require_manager),
    key: uuid.UUID = Depends(idempotency_key),
    factory=Depends(get_factory),
):
    outcome = await svc.cancel_session(
        factory, actor, session_id, body.reason, request_id=key
    )
    return await management_response(request, outcome)


@router.get("/sessions/{session_id}/roster", response_model=Envelope[RosterView])
async def get_roster(
    request: Request,
    session_id: uuid.UUID,
    actor: Account = Depends(require_manager),
    factory=Depends(get_factory),
):
    view = await svc.roster(factory, actor, session_id)
    return Envelope(data=view, meta=meta(request, session_version=view.version))
