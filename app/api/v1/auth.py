"""Authentication routes: ``POST /auth/login``, ``/auth/refresh`` and
``/auth/logout``. Sets and clears the refresh and CSRF cookies.

Design: TECHNICAL_DESIGN.md Sections 8.2, 9.1 and 9.2.
"""

from fastapi import APIRouter, Cookie, Depends, Header, Request, Response, status

from app.api.dependencies import get_factory, get_settings_dep, limit_login, meta
from app.api.middleware import client_ip_of
from app.schemas.accounts import AccountView
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.common import Envelope
from app.services import auth as auth_service
from app.services.auth import IssuedLogin

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"
REFRESH_PATH = "/api/v1/auth"


def _set_cookies(response: Response, issued: IssuedLogin) -> None:
    max_age = max(0, int((issued.refresh_expires_at - issued.now).total_seconds()))
    response.set_cookie(
        REFRESH_COOKIE,
        issued.refresh_token,
        max_age=max_age,
        path=REFRESH_PATH,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    if issued.csrf_token:
        response.set_cookie(
            CSRF_COOKIE,
            issued.csrf_token,
            max_age=max_age,
            path="/",
            httponly=False,
            secure=True,
            samesite="lax",
        )


def _clear_cookies(response: Response) -> None:
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_PATH, secure=True, httponly=True, samesite="lax"
    )
    response.delete_cookie(CSRF_COOKIE, path="/", secure=True, samesite="lax")


def _token_body(request: Request, issued: IssuedLogin) -> Envelope[TokenResponse]:
    return Envelope(
        data=TokenResponse(
            access_token=issued.access.token,
            expires_in=issued.access.expires_in,
            account=AccountView.of(issued.account),
        ),
        meta=meta(request),
    )


@router.post(
    "/login",
    response_model=Envelope[TokenResponse],
    dependencies=[Depends(limit_login)],
)
async def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    factory=Depends(get_factory),
    settings=Depends(get_settings_dep),
):
    issued = await auth_service.login(
        factory,
        settings,
        email=body.email,
        password=body.password,
        user_agent=request.headers.get("user-agent"),
        client_ip=client_ip_of(request),
    )
    _set_cookies(response, issued)
    return _token_body(request, issued)


@router.post("/refresh", response_model=Envelope[TokenResponse])
async def refresh(
    request: Request,
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    x_csrf_token: str | None = Header(default=None),
    factory=Depends(get_factory),
    settings=Depends(get_settings_dep),
):
    issued = await auth_service.refresh(
        factory, settings, refresh_token=refresh_token, csrf_header=x_csrf_token
    )
    _set_cookies(response, issued)
    return _token_body(request, issued)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def logout(
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    x_csrf_token: str | None = Header(default=None),
    factory=Depends(get_factory),
):
    await auth_service.logout(
        factory, refresh_token=refresh_token, csrf_header=x_csrf_token
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_cookies(response)
    return response
