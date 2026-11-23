import asyncio

from app.db.participation_store import capacity_violations
from app.domain.enums import Role
from app.models.participation import Participation
from tests.integration.conftest import idem, published_session


async def _setup(client, make_user, capacity=1):
    _, org = await make_user(Role.ORGANIZER)
    _, session_id = await published_session(client, org, capacity=capacity)
    return session_id


async def test_book_hold_then_waitlist_required(client, make_user, session_factory):
    sid = await _setup(client, make_user, capacity=1)
    _, a = await make_user()
    _, b = await make_user()
    r = await client.post(f"/api/v1/sessions/{sid}/book", headers=a | idem())
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["state"] == "HELD" and data["deadline_at"].endswith("Z")
    assert r.json()["meta"]["session_version"] == data["version"]

    r = await client.post(f"/api/v1/sessions/{sid}/book", headers=b | idem())
    assert r.status_code == 409 and r.json()["error"]["code"] == "WAITLIST_REQUIRED"
    assert r.json()["error"]["details"]["availability"]["available_count"] == 0


async def test_repeat_book_returns_existing(client, make_user):
    sid = await _setup(client, make_user, capacity=2)
    _, a = await make_user()
    first = await client.post(f"/api/v1/sessions/{sid}/book", headers=a | idem())
    second = await client.post(f"/api/v1/sessions/{sid}/book", headers=a | idem())
    assert second.status_code == 200
    assert second.json()["data"]["id"] == first.json()["data"]["id"]


async def test_same_key_replays(client, make_user):
    sid = await _setup(client, make_user, capacity=2)
    _, a = await make_user()
    h = a | idem()
    r1 = await client.post(f"/api/v1/sessions/{sid}/book", headers=h)
    r2 = await client.post(f"/api/v1/sessions/{sid}/book", headers=h)
    assert (r1.status_code, r1.json()["data"]) == (r2.status_code, r2.json()["data"])


async def test_missing_key_is_422_and_anonymous_is_401(client, make_user):
    sid = await _setup(client, make_user)
    _, a = await make_user()
    assert (
        await client.post(f"/api/v1/sessions/{sid}/book", headers=a)
    ).status_code == 422
    assert (
        await client.post(f"/api/v1/sessions/{sid}/book", headers=idem())
    ).status_code == 401


async def test_cannot_jump_queue(client, make_user):
    sid = await _setup(client, make_user, capacity=1)
    _, a = await make_user()
    _, b = await make_user()
    _, c = await make_user()
    held = (
        await client.post(f"/api/v1/sessions/{sid}/book", headers=a | idem())
    ).json()["data"]
    assert (
        await client.post(f"/api/v1/sessions/{sid}/waitlist", headers=b | idem())
    ).status_code == 201
    await client.post(f"/api/v1/participations/{held['id']}/cancel", headers=a | idem())
    r = await client.post(f"/api/v1/sessions/{sid}/book", headers=c | idem())
    assert r.json()["error"]["code"] == "WAITLIST_REQUIRED"


async def test_waitlist_when_seat_free_says_book_directly(client, make_user):
    sid = await _setup(client, make_user, capacity=1)
    _, a = await make_user()
    r = await client.post(f"/api/v1/sessions/{sid}/waitlist", headers=a | idem())
    assert (
        r.status_code == 409 and r.json()["error"]["code"] == "DIRECT_BOOKING_AVAILABLE"
    )


async def test_confirm_success_failure_and_expiry(
    app, settings, client, make_user, session_factory
):
    app.state.settings = settings.model_copy(update={"hold_validity_seconds": 1})
    sid = await _setup(client, make_user, capacity=2)
    _, a = await make_user()
    p = (await client.post(f"/api/v1/sessions/{sid}/book", headers=a | idem())).json()[
        "data"
    ]
    url = f"/api/v1/participations/{p['id']}/confirm"

    r = await client.post(
        url, headers=a | idem(), json={"simulation_result": "failure"}
    )
    assert r.status_code == 200 and r.json()["data"]["state"] == "HELD"
    assert r.json()["data"]["deadline_at"] == p["deadline_at"]

    await asyncio.sleep(1.2)
    r = await client.post(
        url, headers=a | idem(), json={"simulation_result": "success"}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "PARTICIPATION_EXPIRED"
    async with session_factory() as db:
        assert (await db.get(Participation, p["id"])).state == "EXPIRED"
        assert await capacity_violations(db) == []


async def test_other_users_participation_is_404(client, make_user):
    sid = await _setup(client, make_user, capacity=2)
    _, a = await make_user()
    _, b = await make_user()
    p = (await client.post(f"/api/v1/sessions/{sid}/book", headers=a | idem())).json()[
        "data"
    ]
    assert (
        await client.get(f"/api/v1/participations/{p['id']}", headers=b)
    ).status_code == 404
    r = await client.post(
        f"/api/v1/participations/{p['id']}/cancel", headers=b | idem()
    )
    assert r.status_code == 404


async def test_repeat_cancel_releases_once(client, make_user):
    sid = await _setup(client, make_user, capacity=1)
    _, a = await make_user()
    p = (await client.post(f"/api/v1/sessions/{sid}/book", headers=a | idem())).json()[
        "data"
    ]
    url = f"/api/v1/participations/{p['id']}/cancel"
    v1 = (await client.post(url, headers=a | idem())).json()["meta"]["session_version"]
    v2 = (await client.post(url, headers=a | idem())).json()["meta"]["session_version"]
    assert v1 == v2
