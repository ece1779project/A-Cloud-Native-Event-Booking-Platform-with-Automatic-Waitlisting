"""Event routes: public list and detail, and organizer create, edit, publish,
cancel and ``POST /events/{event_id}/sessions``.

Design: TECHNICAL_DESIGN.md Section 8.3.
"""

import uuid

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.dependencies import (
    get_factory,
    get_optional_account,
    meta,
    publish_after_commit,
    require_manager,
)
from app.core.constants import EVENT_LIST_PAGE_SIZE
from app.models.account import Account
from app.schemas.common import Envelope, Page
from app.schemas.events import EventCreate, EventPatch, EventView
from app.schemas.sessions import CancelRequest, SessionCreate, SessionView
from app.services import events as svc
from app.services import sessions as session_svc

router = APIRouter(tags=["events"])

# TODO(idempotency): doc/openapi.yaml requires Idempotency-Key on the management
# commands below too. Not wired yet; same open item as PATCH /admin/accounts.

Limit = Query(EVENT_LIST_PAGE_SIZE, ge=1, le=100)


@router.get("/events", response_model=Envelope[Page[EventView]])
async def list_events(
    request: Request,
    q: str | None = Query(None, max_length=100),
    open: bool = False,
    cursor: str | None = None,
    limit: int = Limit,
    factory=Depends(get_factory),
):
    page = await svc.list_events(
        factory, q=q, open_only=open, cursor=cursor, limit=limit
    )
    return Envelope(data=page, meta=meta(request))


@router.get("/me/events", response_model=Envelope[Page[EventView]])
async def list_my_events(
    request: Request,
    cursor: str | None = None,
    limit: int = Limit,
    actor: Account = Depends(require_manager),
    factory=Depends(get_factory),
):
    page = await svc.list_my_events(factory, actor, cursor=cursor, limit=limit)
    return Envelope(data=page, meta=meta(request))


@router.get("/events/{event_id}", response_model=Envelope[EventView])
async def get_event(
    request: Request,
    event_id: uuid.UUID,
    viewer: Account | None = Depends(get_optional_account),
    factory=Depends(get_factory),
):
    return Envelope(
        data=await svc.get_event(factory, viewer, event_id), meta=meta(request)
    )


@router.post(
    "/events", status_code=status.HTTP_201_CREATED, response_model=Envelope[EventView]
)
async def create_event(
    request: Request,
    body: EventCreate,
    actor: Account = Depends(require_manager),
    factory=Depends(get_factory),
):
    return Envelope(
        data=await svc.create_event(factory, actor, body), meta=meta(request)
    )


@router.patch("/events/{event_id}", response_model=Envelope[EventView])
async def patch_event(
    request: Request,
    event_id: uuid.UUID,
    body: EventPatch,
    actor: Account = Depends(require_manager),
    factory=Depends(get_factory),
):
    return Envelope(
        data=await svc.patch_event(factory, actor, event_id, body), meta=meta(request)
    )


@router.post("/events/{event_id}/publish", response_model=Envelope[EventView])
async def publish_event(
    request: Request,
    event_id: uuid.UUID,
    actor: Account = Depends(require_manager),
    factory=Depends(get_factory),
):
    view, changes = await svc.publish_event(factory, actor, event_id)
    await publish_after_commit(request, changes)
    return Envelope(data=view, meta=meta(request))


@router.post("/events/{event_id}/cancel", response_model=Envelope[EventView])
async def cancel_event(
    request: Request,
    event_id: uuid.UUID,
    body: CancelRequest,
    actor: Account = Depends(require_manager),
    factory=Depends(get_factory),
):
    view, changes = await svc.cancel_event(factory, actor, event_id, body.reason)
    await publish_after_commit(request, changes)
    return Envelope(data=view, meta=meta(request))


@router.post(
    "/events/{event_id}/sessions",
    status_code=status.HTTP_201_CREATED,
    response_model=Envelope[SessionView],
)
async def create_session(
    request: Request,
    event_id: uuid.UUID,
    body: SessionCreate,
    actor: Account = Depends(require_manager),
    factory=Depends(get_factory),
):
    view = await session_svc.create_session(factory, actor, event_id, body)
    return Envelope(data=view, meta=meta(request, session_version=view.version))
