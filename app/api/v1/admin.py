"""Administrator routes: ``/admin/accounts`` and
``POST /admin/participations/{participation_id}/cancel``.

Design: TECHNICAL_DESIGN.md Sections 8.2 and 8.4.
"""

import uuid

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_factory, meta, require_admin
from app.core.errors import AppError, ErrorCode
from app.domain.enums import Role
from app.models.account import Account
from app.schemas.accounts import AccountView, AdminAccountPatch
from app.schemas.common import Envelope, Page, decode_cursor, encode_cursor
from app.services import accounts as accounts_service

router = APIRouter(prefix="/admin", tags=["admin"])


def _cursor_id(cursor: str | None) -> uuid.UUID | None:
    if not cursor:
        return None
    try:
        return uuid.UUID(str(decode_cursor(cursor)["id"]))
    except (KeyError, ValueError) as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, "Invalid cursor.") from exc


@router.get("/accounts", response_model=Envelope[Page[AccountView]])
async def list_accounts(
    request: Request,
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    _admin: Account = Depends(require_admin),
    factory=Depends(get_factory),
):
    rows = await accounts_service.list_accounts(factory, after_id=_cursor_id(cursor), limit=limit)
    next_cursor = encode_cursor({"id": str(rows[limit - 1].id)}) if len(rows) > limit else None
    return Envelope(
        data=Page(items=[AccountView.of(a) for a in rows[:limit]], next_cursor=next_cursor),
        meta=meta(request),
    )


@router.patch("/accounts/{account_id}", response_model=Envelope[AccountView])
async def patch_account(
    request: Request,
    account_id: uuid.UUID,
    body: AdminAccountPatch,
    admin: Account = Depends(require_admin),
    factory=Depends(get_factory),
):
    # TODO(idempotency): require Idempotency-Key as doc/openapi.yaml specifies.
    account = await accounts_service.admin_patch_account(
        factory,
        actor=admin,
        target_id=account_id,
        role=Role(body.role) if body.role else None,
        disabled=body.disabled,
        reason=body.reason,
    )
    return Envelope(data=AccountView.of(account), meta=meta(request))
