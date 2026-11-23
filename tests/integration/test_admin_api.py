from sqlalchemy import select

from app.domain.enums import Role
from app.models.auth_session import AuthSession
from app.models.management_action import ManagementAction


async def test_non_admin_forbidden(client, make_user):
    _, attendee = await make_user(Role.ATTENDEE)
    _, organizer = await make_user(Role.ORGANIZER)
    for headers in (attendee, organizer):
        assert (
            await client.get("/api/v1/admin/accounts", headers=headers)
        ).status_code == 403
    assert (await client.get("/api/v1/admin/accounts")).status_code == 401


async def test_promote_to_organizer_is_audited(client, make_user, session_factory):
    _, admin = await make_user(Role.ADMIN)
    target, _ = await make_user(Role.ATTENDEE)
    r = await client.patch(
        f"/api/v1/admin/accounts/{target.id}",
        headers=admin,
        json={"role": "ORGANIZER", "reason": "runs workshops"},
    )
    assert r.status_code == 200 and r.json()["data"]["role"] == "ORGANIZER"
    async with session_factory() as db:
        action = await db.scalar(select(ManagementAction))
    assert action.action == "ROLE_CHANGED" and action.target_account_id == target.id


async def test_disable_revokes_sessions_and_blocks_access(
    client, make_user, session_factory
):
    _, admin = await make_user(Role.ADMIN)
    target, target_headers = await make_user(Role.ATTENDEE)
    r = await client.patch(
        f"/api/v1/admin/accounts/{target.id}",
        headers=admin,
        json={"disabled": True, "reason": "abuse"},
    )
    assert r.status_code == 200
    assert (await client.get("/api/v1/me", headers=target_headers)).status_code == 403
    async with session_factory() as db:
        rows = list(
            await db.scalars(
                select(AuthSession).where(AuthSession.account_id == target.id)
            )
        )
    assert rows and all(r.revoke_reason == "ACCOUNT_DISABLED" for r in rows)


async def test_patch_requires_reason_and_a_change(client, make_user):
    _, admin = await make_user(Role.ADMIN)
    target, _ = await make_user()
    url = f"/api/v1/admin/accounts/{target.id}"
    assert (
        await client.patch(url, headers=admin, json={"role": "ORGANIZER"})
    ).status_code == 422
    assert (
        await client.patch(url, headers=admin, json={"reason": "x"})
    ).status_code == 422
    assert (
        await client.patch(
            url, headers=admin, json={"role": "ORGANIZER", "reason": "  "}
        )
    ).status_code == 422


async def test_unknown_account_404(client, make_user):
    _, admin = await make_user(Role.ADMIN)
    r = await client.patch(
        "/api/v1/admin/accounts/00000000-0000-7000-8000-000000000000",
        headers=admin,
        json={"disabled": True, "reason": "x"},
    )
    assert r.status_code == 404


async def test_cannot_change_self_or_another_admin(client, make_user):
    me, admin = await make_user(Role.ADMIN)
    other, _ = await make_user(Role.ADMIN)
    for target in (me, other):
        r = await client.patch(
            f"/api/v1/admin/accounts/{target.id}",
            headers=admin,
            json={"disabled": True, "reason": "x"},
        )
        assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"


async def test_admin_role_cannot_be_granted(client, make_user):
    _, admin = await make_user(Role.ADMIN)
    target, _ = await make_user()
    r = await client.patch(
        f"/api/v1/admin/accounts/{target.id}",
        headers=admin,
        json={"role": "ADMIN", "reason": "x"},
    )
    assert r.status_code == 422


async def test_enable_after_disable_is_audited_and_old_tokens_stay_revoked(
    client, make_user, session_factory
):
    _, admin = await make_user(Role.ADMIN)
    target, _ = await make_user()
    url = f"/api/v1/admin/accounts/{target.id}"
    assert (
        await client.patch(url, headers=admin, json={"disabled": True, "reason": "a"})
    ).status_code == 200
    r = await client.patch(url, headers=admin, json={"disabled": False, "reason": "b"})
    assert r.status_code == 200 and r.json()["data"]["disabled"] is False
    async with session_factory() as db:
        actions = [
            a.action
            for a in await db.scalars(
                select(ManagementAction).order_by(ManagementAction.occurred_at)
            )
        ]
        live = await db.scalar(
            select(AuthSession).where(
                AuthSession.account_id == target.id, AuthSession.revoked_at.is_(None)
            )
        )
    assert actions == ["ACCOUNT_DISABLED", "ACCOUNT_ENABLED"]
    assert live is None


async def test_unchanged_value_writes_no_audit(client, make_user, session_factory):
    _, admin = await make_user(Role.ADMIN)
    target, _ = await make_user(Role.ATTENDEE)
    r = await client.patch(
        f"/api/v1/admin/accounts/{target.id}",
        headers=admin,
        json={"role": "ATTENDEE", "reason": "noop"},
    )
    assert r.status_code == 200
    async with session_factory() as db:
        assert await db.scalar(select(ManagementAction)) is None


async def test_list_accounts_paginates(client, make_user):
    _, admin = await make_user(Role.ADMIN)
    for _ in range(2):
        await make_user()
    seen, cursor = [], None
    while True:
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        r = await client.get("/api/v1/admin/accounts", headers=admin, params=params)
        assert r.status_code == 200
        page = r.json()["data"]
        seen += [a["id"] for a in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 3 and seen == sorted(seen)
    r = await client.get(
        "/api/v1/admin/accounts", headers=admin, params={"cursor": "garbage"}
    )
    assert r.status_code == 422
