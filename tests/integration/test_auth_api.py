import asyncio

from sqlalchemy import select, update

from app.domain.enums import Role
from app.models.account import Account
from app.models.auth_session import AuthSession
from tests.integration.conftest import PASSWORD


async def _login(client, make_user):
    acct, headers = await make_user(Role.ATTENDEE)
    csrf = client.cookies.get("csrf_token")
    return acct, headers, csrf


async def test_login_sets_cookies_and_me_works(client, make_user):
    acct, headers, csrf = await _login(client, make_user)
    assert csrf and client.cookies.get("refresh_token")
    r = await client.get("/api/v1/me", headers=headers)
    assert r.status_code == 200 and r.json()["data"]["id"] == str(acct.id)


async def test_wrong_password(client, make_user):
    acct, _, _ = await _login(client, make_user)
    r = await client.post("/api/v1/auth/login", json={"email": acct.email, "password": "nope-nope-nope"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_unknown_email_same_error(client):
    r = await client.post("/api/v1/auth/login", json={"email": "ghost@example.com", "password": PASSWORD})
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_refresh_rotates_and_keeps_expiry(client, make_user, session_factory):
    acct, _, csrf = await _login(client, make_user)
    old = client.cookies.get("refresh_token")
    r = await client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    assert client.cookies.get("refresh_token") != old
    async with session_factory() as db:
        rows = list(await db.scalars(select(AuthSession).where(AuthSession.account_id == acct.id)))
    assert len(rows) == 2
    assert len({r.family_id for r in rows}) == 1
    assert len({r.expires_at for r in rows}) == 1
    assert sum(r.rotated_at is not None for r in rows) == 1


async def test_refresh_requires_csrf(client, make_user):
    await _login(client, make_user)
    r = await client.post("/api/v1/auth/refresh")
    assert r.status_code == 403
    r = await client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": "wrong"})
    assert r.status_code == 403


async def test_reuse_within_grace_is_409_without_revoking(client, make_user, session_factory):
    _, _, csrf = await _login(client, make_user)
    old = client.cookies.get("refresh_token")
    assert (await client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf})).status_code == 200
    r = await client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf},
                          cookies={"refresh_token": old})
    assert r.status_code == 409 and r.json()["error"]["code"] == "REFRESH_ALREADY_ROTATED"
    async with session_factory() as db:
        assert await db.scalar(select(AuthSession).where(AuthSession.revoked_at.is_not(None))) is None


async def test_reuse_after_grace_revokes_family(client, make_user, session_factory, monkeypatch):
    monkeypatch.setattr("app.services.auth.REFRESH_ROTATION_GRACE_SECONDS", 0)
    _, _, csrf = await _login(client, make_user)
    old = client.cookies.get("refresh_token")
    assert (await client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf})).status_code == 200
    r = await client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf},
                          cookies={"refresh_token": old})
    assert r.status_code == 401
    async with session_factory() as db:
        rows = list(await db.scalars(select(AuthSession)))
    assert all(r.revoke_reason == "REUSE_DETECTED" for r in rows)
    r = await client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 401


async def test_logout_revokes_and_clears(client, make_user):
    _, _, csrf = await _login(client, make_user)
    r = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 204
    r = await client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 401


async def test_concurrent_refresh_only_one_rotates(client, make_user):
    _, _, csrf = await _login(client, make_user)
    rt = client.cookies.get("refresh_token")
    results = await asyncio.gather(*[
        client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf}, cookies={"refresh_token": rt})
        for _ in range(5)
    ])
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) == 1 and set(codes) <= {200, 409}


async def test_disabled_account_cannot_use_access_token(client, make_user, session_factory):
    acct, headers, _ = await _login(client, make_user)
    async with session_factory.begin() as db:
        await db.execute(update(Account).where(Account.id == acct.id).values(disabled_at=Account.created_at))
    r = await client.get("/api/v1/me", headers=headers)
    assert r.status_code == 403 and r.json()["error"]["code"] == "ACCOUNT_DISABLED"


async def test_login_rate_limited_per_ip(client, app, make_user):
    acct, _, _ = await _login(client, make_user)
    app.state.settings = app.state.settings.model_copy(update={"login_limit_per_minute": 3})
    codes = []
    for _ in range(3):
        r = await client.post("/api/v1/auth/login", json={"email": acct.email, "password": "wrong-password"})
        codes.append(r.status_code)
    assert codes == [401, 401, 429]
    assert r.json()["error"]["code"] == "RATE_LIMITED" and int(r.headers["Retry-After"]) >= 1
