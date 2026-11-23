"""Management commands and the participation paths test_events_api.py and
test_booking_api.py leave out: publication rules, schedule freeze, capacity
changes, session and event cancellation, confirmation, administrator
cancel, the stored-result lookup, the roster and the attendee's list."""

import pytest
from sqlalchemy import func, select

from app.domain.enums import Role
from app.models.management_action import ManagementAction
from app.models.participation import Participation
from tests.integration.conftest import idem, published_session, session_body

pytestmark = pytest.mark.usefixtures("capacity_invariant")

EVENT = {"title": "Workshop", "description": "d", "location": "Toronto"}


async def _book(client, sid, h):
    return await client.post(f"/api/v1/sessions/{sid}/book", headers=h | idem())


async def _wait(client, sid, h):
    return await client.post(f"/api/v1/sessions/{sid}/waitlist", headers=h | idem())


async def _mine(client, sid, h) -> dict:
    return (
        await client.get(f"/api/v1/sessions/{sid}/my-participation", headers=h)
    ).json()["data"]


async def _full_session(client, make_user, *, waiters=1):
    """Capacity 1: one holder and ``waiters`` people in the queue."""
    _, org = await make_user(Role.ORGANIZER)
    event_id, sid = await published_session(client, org, capacity=1)
    _, holder = await make_user()
    held = (await _book(client, sid, holder)).json()["data"]
    queue = []
    for _ in range(waiters):
        _, h = await make_user()
        assert (await _wait(client, sid, h)).status_code == 201
        queue.append(h)
    return org, event_id, sid, holder, held, queue


# ---- Publication ----


async def test_new_session_on_published_event_stays_draft_until_publish(
    client, make_user
):
    _, org = await make_user(Role.ORGANIZER)
    event_id, _ = await published_session(client, org)
    s = (
        await client.post(
            f"/api/v1/events/{event_id}/sessions",
            headers=org | idem(),
            json=session_body(),
        )
    ).json()["data"]
    assert s["status"] == "DRAFT"
    assert (await client.get(f"/api/v1/sessions/{s['id']}")).status_code == 404

    assert (
        await client.post(f"/api/v1/events/{event_id}/publish", headers=org | idem())
    ).status_code == 200
    assert (await client.get(f"/api/v1/sessions/{s['id']}")).json()["data"][
        "status"
    ] == "PUBLISHED"
    # Nothing left to publish: 200 unchanged.
    assert (
        await client.post(f"/api/v1/events/{event_id}/publish", headers=org | idem())
    ).status_code == 200


async def test_session_whose_registration_closed_is_not_published(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    e = (await client.post("/api/v1/events", headers=org | idem(), json=EVENT)).json()[
        "data"
    ]
    await client.post(
        f"/api/v1/events/{e['id']}/sessions",
        headers=org | idem(),
        json=session_body(opens=-3, closes=-2, starts=1, ends=2),
    )
    r = await client.post(f"/api/v1/events/{e['id']}/publish", headers=org | idem())
    assert (
        r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE_TRANSITION"
    )


async def test_my_events_lists_drafts_for_owner_only(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    _, other = await make_user(Role.ORGANIZER)
    _, att = await make_user()
    await client.post("/api/v1/events", headers=org | idem(), json=EVENT)
    mine = (await client.get("/api/v1/me/events", headers=org)).json()["data"]["items"]
    assert [e["status"] for e in mine] == ["DRAFT"]
    assert (await client.get("/api/v1/me/events", headers=other)).json()["data"][
        "items"
    ] == []
    assert (await client.get("/api/v1/me/events", headers=att)).status_code == 403


# ---- Session edits ----


async def test_schedule_frozen_once_anyone_registered(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    _, sid = await published_session(client, org)
    _, a = await make_user()
    await _book(client, sid, a)
    later = session_body(starts=5, ends=6)
    r = await client.patch(
        f"/api/v1/sessions/{sid}",
        headers=org | idem(),
        json={"starts_at": later["starts_at"], "ends_at": later["ends_at"]},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "SCHEDULE_FROZEN"
    assert (
        await client.patch(
            f"/api/v1/sessions/{sid}", headers=org | idem(), json={"name": "Evening"}
        )
    ).status_code == 200


async def test_merged_times_must_stay_ordered(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    _, sid = await published_session(client, org)
    too_late = session_body(ends=10)["ends_at"]
    r = await client.patch(
        f"/api/v1/sessions/{sid}", headers=org | idem(), json={"starts_at": too_late}
    )
    assert r.status_code == 422


async def test_capacity_increase_offers_seat_to_queue_head(client, make_user):
    org, _, sid, _, _, (b, c) = await _full_session(client, make_user, waiters=2)
    before = (await client.get(f"/api/v1/sessions/{sid}")).json()["data"]["version"]
    r = await client.patch(
        f"/api/v1/sessions/{sid}", headers=org | idem(), json={"capacity": 2}
    )
    assert r.status_code == 200
    assert r.json()["data"]["version"] == before + 1  # one bump for change + promotion
    assert (await _mine(client, sid, b))["state"] == "OFFERED"
    assert (await _mine(client, sid, c))["queue_position"] == 1


async def test_capacity_cannot_drop_below_occupied(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    _, sid = await published_session(client, org, capacity=2)
    for _ in range(2):
        _, h = await make_user()
        await _book(client, sid, h)
    r = await client.patch(
        f"/api/v1/sessions/{sid}", headers=org | idem(), json={"capacity": 1}
    )
    assert (
        r.status_code == 409 and r.json()["error"]["code"] == "CAPACITY_BELOW_OCCUPIED"
    )


async def test_empty_patch_is_422(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    event_id, sid = await published_session(client, org)
    assert (
        await client.patch(f"/api/v1/sessions/{sid}", headers=org | idem(), json={})
    ).status_code == 422
    assert (
        await client.patch(f"/api/v1/events/{event_id}", headers=org | idem(), json={})
    ).status_code == 422


# ---- Cancellation ----


async def test_cancel_session_ends_everyone_and_blocks_booking(
    client, make_user, session_factory
):
    org, _, sid, holder, held, (b,) = await _full_session(client, make_user)
    url = f"/api/v1/sessions/{sid}/cancel"
    r = await client.post(url, headers=org | idem(), json={"reason": "Room flooded"})
    assert r.status_code == 200 and r.json()["data"]["status"] == "CANCELLED"
    for h in (holder, b):
        p = await _mine(client, sid, h)
        assert (p["state"], p["end_reason"], p["historical"]) == (
            "CANCELLED",
            "SESSION_CANCELLED",
            True,
        )

    _, late = await make_user()
    assert (await _book(client, sid, late)).json()["error"][
        "code"
    ] == "SESSION_CANCELLED"
    r = await client.post(
        f"/api/v1/participations/{held['id']}/confirm",
        headers=holder | idem(),
        json={"simulation_result": "success"},
    )
    assert r.json()["error"]["code"] == "SESSION_CANCELLED"

    again = await client.post(
        url, headers=org | idem(), json={"reason": "Room flooded"}
    )
    assert (
        again.json()["data"]["version"] == r.json()["meta"]["session_version"]
    )  # no second bump
    async with session_factory() as db:
        n = await db.scalar(select(func.count()).select_from(ManagementAction))
    assert n == 1


async def test_cancel_event_is_one_action_and_labels_history(
    client, make_user, session_factory
):
    org, event_id, sid, holder, _, _ = await _full_session(client, make_user)
    r = await client.post(
        f"/api/v1/events/{event_id}/cancel",
        headers=org | idem(),
        json={"reason": "Speaker ill"},
    )
    assert r.status_code == 200 and r.json()["data"]["status"] == "CANCELLED"
    p = await _mine(client, sid, holder)
    assert (p["end_reason"], p["display_label"]) == (
        "EVENT_CANCELLED",
        "Event cancelled",
    )
    assert (
        await client.patch(
            f"/api/v1/events/{event_id}", headers=org | idem(), json={"title": "x"}
        )
    ).json()["error"]["code"] == "EVENT_CANCELLED"
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(ManagementAction)) == 1


async def test_cancel_requires_reason(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    event_id, _ = await published_session(client, org)
    r = await client.post(
        f"/api/v1/events/{event_id}/cancel",
        headers=org | idem(),
        json={"reason": "   "},
    )
    assert r.status_code == 422


# ---- Attendee commands ----


async def test_confirm_success_bumps_version_and_repeats_unchanged(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    _, sid = await published_session(client, org)
    _, a = await make_user()
    p = (await _book(client, sid, a)).json()["data"]
    url = f"/api/v1/participations/{p['id']}/confirm"
    r = await client.post(
        url, headers=a | idem(), json={"simulation_result": "success"}
    )
    assert r.status_code == 200
    assert (r.json()["data"]["state"], r.json()["meta"]["result"]) == (
        "CONFIRMED",
        "CONFIRMED",
    )
    assert r.json()["meta"]["session_version"] == p["version"] + 1

    again = await client.post(
        url, headers=a | idem(), json={"simulation_result": "success"}
    )
    assert again.status_code == 200
    assert (
        again.json()["meta"]["session_version"] == r.json()["meta"]["session_version"]
    )


async def test_payment_failure_is_200_with_result_code(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    _, sid = await published_session(client, org)
    _, a = await make_user()
    p = (await _book(client, sid, a)).json()["data"]
    r = await client.post(
        f"/api/v1/participations/{p['id']}/confirm",
        headers=a | idem(),
        json={"simulation_result": "failure"},
    )
    assert (
        r.status_code == 200
        and r.json()["meta"]["result"] == "PAYMENT_SIMULATION_FAILED"
    )
    assert r.json()["meta"]["session_version"] == p["version"]  # nothing changed


async def test_confirm_rejects_payment_fields(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    _, sid = await published_session(client, org)
    _, a = await make_user()
    p = (await _book(client, sid, a)).json()["data"]
    r = await client.post(
        f"/api/v1/participations/{p['id']}/confirm",
        headers=a | idem(),
        json={"simulation_result": "success", "card_number": "4111"},
    )
    assert r.status_code == 422


async def test_cancel_offer_is_invalid_use_decline(client, make_user):
    _, _, sid, holder, held, (b,) = await _full_session(client, make_user)
    await client.post(
        f"/api/v1/participations/{held['id']}/cancel", headers=holder | idem()
    )
    offer = await _mine(client, sid, b)
    assert offer["state"] == "OFFERED"
    r = await client.post(
        f"/api/v1/participations/{offer['id']}/cancel", headers=b | idem()
    )
    assert r.json()["error"]["code"] == "INVALID_STATE_TRANSITION"


async def test_join_waitlist_reports_position(client, make_user):
    _, _, sid, _, _, queue = await _full_session(client, make_user, waiters=3)
    assert [(await _mine(client, sid, h))["queue_position"] for h in queue] == [1, 2, 3]


# ---- Administrator cancel ----


async def test_admin_cancel_frees_seat_and_writes_audit(
    client, make_user, session_factory
):
    _, _, sid, _, held, (b,) = await _full_session(client, make_user)
    _, admin = await make_user(Role.ADMIN)
    url = f"/api/v1/admin/participations/{held['id']}/cancel"
    assert (
        await client.post(url, headers=admin, json={"reason": "Duplicate"})
    ).status_code == 422
    r = await client.post(url, headers=admin | idem(), json={"reason": "Duplicate"})
    assert r.status_code == 200 and r.json()["data"]["end_reason"] == "ADMIN_CANCELLED"
    assert (await _mine(client, sid, b))["state"] == "OFFERED"
    async with session_factory() as db:
        action = await db.scalar(select(ManagementAction))
        p = await db.get(Participation, held["id"])
    assert action.target_participation_id == p.id


async def test_attendee_cannot_use_admin_cancel(client, make_user):
    _, _, _, holder, held, _ = await _full_session(client, make_user, waiters=0)
    r = await client.post(
        f"/api/v1/admin/participations/{held['id']}/cancel",
        headers=holder | idem(),
        json={"reason": "x"},
    )
    assert r.status_code == 403


# ---- Stored results, roster, my list ----


async def test_request_result_replays_original_and_is_private(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    _, sid = await published_session(client, org)
    _, a = await make_user()
    _, b = await make_user()
    key = idem()
    first = await client.post(f"/api/v1/sessions/{sid}/book", headers=a | key)
    url = f"/api/v1/requests/{key['Idempotency-Key']}"
    r = await client.get(url, headers=a)
    assert (r.status_code, r.json()["data"]) == (
        first.status_code,
        first.json()["data"],
    )
    assert (await client.get(url, headers=b)).status_code == 404


async def test_rejection_is_stored_and_replayed(client, make_user):
    _, _, sid, _, _, _ = await _full_session(client, make_user, waiters=0)
    _, late = await make_user()
    key = idem()
    r = await client.post(f"/api/v1/sessions/{sid}/book", headers=late | key)
    assert r.status_code == 409
    stored = await client.get(
        f"/api/v1/requests/{key['Idempotency-Key']}", headers=late
    )
    assert (stored.status_code, stored.json()["error"]["code"]) == (
        409,
        "WAITLIST_REQUIRED",
    )


async def test_roster_counts_and_positions(client, make_user):
    org, _, sid, _, _, _ = await _full_session(client, make_user, waiters=2)
    data = (await client.get(f"/api/v1/sessions/{sid}/roster", headers=org)).json()[
        "data"
    ]
    assert data["counts"] == {"held": 1, "offered": 0, "confirmed": 0, "waitlisted": 2}
    assert [w["position"] for w in data["waitlisted"]] == [1, 2]
    assert data["held"][0].get("position") is None


async def test_my_participations_puts_deadlines_first_and_pages(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    _, a = await make_user()
    sids = [(await published_session(client, org, capacity=1))[1] for _ in range(3)]
    for sid in sids[:2]:
        await _book(client, sid, a)
    p = (
        await client.get(f"/api/v1/sessions/{sids[0]}/my-participation", headers=a)
    ).json()["data"]
    await client.post(f"/api/v1/participations/{p['id']}/cancel", headers=a | idem())
    await _book(client, sids[2], a)

    first = (
        await client.get("/api/v1/me/participations", headers=a, params={"limit": 2})
    ).json()["data"]
    assert [i["state"] for i in first["items"]] == ["HELD", "HELD"]
    second = (
        await client.get(
            "/api/v1/me/participations",
            headers=a,
            params={"limit": 2, "cursor": first["next_cursor"]},
        )
    ).json()["data"]
    assert [i["state"] for i in second["items"]] == ["CANCELLED"] and second[
        "next_cursor"
    ] is None


# ---- Idempotency-Key on management commands ----


async def test_management_command_without_key_is_422(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    r = await client.post("/api/v1/events", headers=org, json=EVENT)
    assert r.status_code == 422
    assert r.json()["error"]["details"] == {"header": "Idempotency-Key"}


async def test_create_event_retry_replays_without_second_event(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    h = org | idem()
    first = await client.post("/api/v1/events", headers=h, json=EVENT)
    again = await client.post("/api/v1/events", headers=h, json=EVENT)
    assert first.status_code == again.status_code == 201
    assert again.json()["data"] == first.json()["data"]
    assert first.json()["meta"]["result"] is None
    mine = (await client.get("/api/v1/me/events", headers=org)).json()["data"]
    assert len(mine["items"]) == 1


async def test_same_key_with_different_body_is_409(client, make_user):
    _, org = await make_user(Role.ORGANIZER)
    h = org | idem()
    await client.post("/api/v1/events", headers=h, json=EVENT)
    r = await client.post("/api/v1/events", headers=h, json=EVENT | {"title": "x"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


async def test_rejected_command_leaves_key_unused(client, make_user):
    """A 409 rolls back, so the same key can carry the corrected request."""
    _, org = await make_user(Role.ORGANIZER)
    e = (await client.post("/api/v1/events", headers=org | idem(), json=EVENT)).json()[
        "data"
    ]
    h = org | idem()
    url = f"/api/v1/events/{e['id']}/publish"
    assert (await client.post(url, headers=h)).status_code == 409  # no session
    await client.post(
        f"/api/v1/events/{e['id']}/sessions", headers=org | idem(), json=session_body()
    )
    assert (await client.post(url, headers=h)).status_code == 200


async def test_session_cancel_retry_writes_one_audit_row(
    client, make_user, session_factory
):
    _, org = await make_user(Role.ORGANIZER)
    _, sid = await published_session(client, org)
    h = org | idem()
    url = f"/api/v1/sessions/{sid}/cancel"
    first = await client.post(url, headers=h, json={"reason": "Room flooded"})
    again = await client.post(url, headers=h, json={"reason": "Room flooded"})
    assert first.status_code == again.status_code == 200
    assert (
        again.json()["meta"]["session_version"]
        == first.json()["meta"]["session_version"]
    )
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(ManagementAction)) == 1
