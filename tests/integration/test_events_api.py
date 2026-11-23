from app.domain.enums import Role
from tests.integration.conftest import idem, published_session, session_body


async def test_attendee_cannot_create_event(client, make_user):
    _, h = await make_user(Role.ATTENDEE)
    r = await client.post(
        "/api/v1/events",
        headers=h | idem(),
        json={"title": "t", "description": "d", "location": "l"},
    )
    assert r.status_code == 403


async def test_draft_hidden_from_others(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    _, other = await make_user(Role.ORGANIZER)
    e = (
        await client.post(
            "/api/v1/events",
            headers=org | idem(),
            json={"title": "t", "description": "d", "location": "l"},
        )
    ).json()["data"]
    assert (await client.get(f"/api/v1/events/{e['id']}")).status_code == 404
    assert (
        await client.get(f"/api/v1/events/{e['id']}", headers=other)
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/events/{e['id']}", headers=org)
    ).status_code == 200


async def test_publish_requires_session(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    e = (
        await client.post(
            "/api/v1/events",
            headers=org | idem(),
            json={"title": "t", "description": "d", "location": "l"},
        )
    ).json()["data"]
    r = await client.post(f"/api/v1/events/{e['id']}/publish", headers=org | idem())
    assert r.status_code == 409


async def test_invalid_times_and_capacity(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    e = (
        await client.post(
            "/api/v1/events",
            headers=org | idem(),
            json={"title": "t", "description": "d", "location": "l"},
        )
    ).json()["data"]
    url = f"/api/v1/events/{e['id']}/sessions"
    assert (
        await client.post(url, headers=org | idem(), json=session_body(capacity=0))
    ).status_code == 422
    assert (
        await client.post(
            url, headers=org | idem(), json=session_body(closes=3, starts=2)
        )
    ).status_code == 422
    bad_zone = session_body() | {"time_zone": "Mars/Base"}
    assert (
        await client.post(url, headers=org | idem(), json=bad_zone)
    ).status_code == 422


async def test_non_owner_organizer_forbidden_admin_allowed(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    _, other = await make_user(Role.ORGANIZER)
    _, admin = await make_user(Role.ADMIN)
    event_id, _ = await published_session(client, org)
    body = {"location": "Elsewhere"}
    assert (
        await client.patch(
            f"/api/v1/events/{event_id}", headers=other | idem(), json=body
        )
    ).status_code == 403
    assert (
        await client.patch(
            f"/api/v1/events/{event_id}", headers=admin | idem(), json=body
        )
    ).status_code == 200


async def test_session_view_counts(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    _, session_id = await published_session(client, org, capacity=3)
    data = (await client.get(f"/api/v1/sessions/{session_id}")).json()["data"]
    assert data["available_count"] == 3 and data["waitlist_count"] == 0
    assert data["registration_status"] == "OPEN"


async def test_list_pagination_and_order(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    for i in range(5):
        await published_session(client, org, starts=2 + i, ends=3 + i)
    first = (await client.get("/api/v1/events", params={"limit": 2})).json()["data"]
    second = (
        await client.get(
            "/api/v1/events", params={"limit": 2, "cursor": first["next_cursor"]}
        )
    ).json()["data"]
    ids = [e["id"] for e in first["items"] + second["items"]]
    assert len(ids) == len(set(ids)) == 4
    starts = [e["next_session_starts_at"] for e in first["items"] + second["items"]]
    assert starts == sorted(starts)


async def test_roster_is_manager_only(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    _, att = await make_user(Role.ATTENDEE)
    _, session_id = await published_session(client, org)
    assert (
        await client.get(f"/api/v1/sessions/{session_id}/roster", headers=att)
    ).status_code == 403
    assert (
        await client.get(f"/api/v1/sessions/{session_id}/roster", headers=org)
    ).status_code == 200
