"""Account routes: ``POST /accounts`` and ``GET /me``.

Design: TECHNICAL_DESIGN.md Section 8.2.
"""

from fastapi import APIRouter, Depends, Request, status

from app.api.dependencies import get_current_account, get_factory, meta
from app.models.account import Account
from app.schemas.accounts import AccountCreate, AccountView
from app.schemas.common import Envelope
from app.services import accounts as accounts_service

router = APIRouter(tags=["accounts"])


@router.post("/accounts", status_code=status.HTTP_201_CREATED, response_model=Envelope[AccountView])
async def create_account(request: Request, body: AccountCreate, factory=Depends(get_factory)):
    account = await accounts_service.register(factory, body)
    return Envelope(data=AccountView.of(account), meta=meta(request))


@router.get("/me", response_model=Envelope[AccountView])
async def me(request: Request, account: Account = Depends(get_current_account)):
    return Envelope(data=AccountView.of(account), meta=meta(request))
