"""Decline, leave, and the repeat rule shared with cancel and administrator
cancel (TECHNICAL_DESIGN 7.3 and 8.4).

Service tests build exact states with the factories; the HTTP tests at the
end go through the routes.
"""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.errors import AppError, ErrorCode
from app.domain.enums import EndReason, Role
from app.domain.enums import ParticipationState as S
from app.models.management_action import ManagementAction
from app.services import booking
from app.services import events as esvc
from app.services import participations as psvc
from app.services import sessions as ssvc
from app.services.idempotency import find_receipt
from tests.factories import (
    make_account,
    make_confirmed,
    make_held,
    make_offered,
    make_scope,
    make_waiting,
)
from tests.helpers import latest_history, reload, session_version
from tests.integration.conftest import idem, published_session

pytestmark = pytest.mark.usefixtures("capacity_invariant")


async def _run(command, session_factory, settings, account, p, key=None):
    return await command(
        session_factory,
        settings,
        account=account,
        participation_id=p.id,
        request_id=key or uuid.uuid4(),
    )


def _reasons(outcome) -> list[str]:
    return [t.reason for change in outcome.changes for t in change.transitions]


async def _position(session_factory, account, p) -> int | None:
    return (await psvc.get_participation(session_factory, account, p.id)).queue_position


# ---- Decline ----


async def test_decline_offers_the_seat_to_the_next_waiter(session_factory, settings):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        a, b, c = [await make_account(db) for _ in range(3)]
        offer = await make_offered(db, session, a, 1)
        next_up = await make_waiting(db, session, b, 2)
        behind = await make_waiting(db, session, c, 3)
    before = await session_version(session_factory, session.id)
    key = uuid.uuid4()

    outcome = await _run(psvc.decline, session_factory, settings, a, offer, key)

    assert (outcome.http_status, outcome.result_code) == (200, "CANCELLED")
    assert outcome.payload["end_reason"] == EndReason.OFFER_DECLINED
    assert outcome.changes[0].cause == "attendee_release"
    assert _reasons(outcome) == ["cancelled", "offer_created"]
    assert await session_version(session_factory, session.id) == before + 1

    (next_up,) = await reload(session_factory, [next_up])
    assert next_up.state == S.OFFERED
    assert next_up.deadline_at - next_up.allocated_at == timedelta(seconds=600)
    declined = await latest_history(session_factory, offer.id)
    promoted = await latest_history(session_factory, next_up.id)
    assert (declined.actor_id, declined.correlation_id) == (a.id, key)
    assert (promoted.actor_id, promoted.correlation_id) == (None, key)
    assert await _position(session_factory, c, behind) == 1


async def test_decline_without_a_waiter_leaves_the_seat_free(session_factory, settings):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        a = await make_account(db)
        offer = await make_offered(db, session, a, 1)

    outcome = await _run(psvc.decline, session_factory, settings, a, offer)

    assert outcome.result_code == "CANCELLED"
    assert _reasons(outcome) == ["cancelled"]


async def test_overdue_offer_expires_and_passes_on(session_factory, settings):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        a, b = await make_account(db), await make_account(db)
        offer = await make_offered(db, session, a, 1, overdue=True)
        next_up = await make_waiting(db, session, b, 2)
    before = await session_version(session_factory, session.id)
    key = uuid.uuid4()

    outcome = await _run(psvc.decline, session_factory, settings, a, offer, key)

    assert (outcome.http_status, outcome.result_code) == (409, "PARTICIPATION_EXPIRED")
    assert _reasons(outcome) == ["expired", "offer_created"]
    assert await session_version(session_factory, session.id) == before + 1
    offer, next_up = await reload(session_factory, [offer, next_up])
    assert (offer.state, offer.end_reason) == (S.EXPIRED, EndReason.OFFER_EXPIRED)
    assert (await latest_history(session_factory, offer.id)).actor_id is None
    assert next_up.state == S.OFFERED
    async with session_factory() as db:
        assert (await find_receipt(db, a.id, key)).http_status == 409


@pytest.mark.parametrize("window", ["closed", "started"])
async def test_overdue_offer_after_close_offers_nothing(
    session_factory, settings, window
):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1, window=window)
        a, b = await make_account(db), await make_account(db)
        offer = await make_offered(db, session, a, 1, overdue=True)
        waiter = await make_waiting(db, session, b, 2)

    outcome = await _run(psvc.decline, session_factory, settings, a, offer)

    assert outcome.result_code == "PARTICIPATION_EXPIRED"
    assert _reasons(outcome) == ["expired"]
    (waiter,) = await reload(session_factory, [waiter])
    assert waiter.state == S.WAITLISTED


async def test_someone_elses_offer_is_404_without_a_receipt(session_factory, settings):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        a, other = await make_account(db), await make_account(db)
        offer = await make_offered(db, session, a, 1)
    key = uuid.uuid4()

    with pytest.raises(AppError) as exc:
        await _run(psvc.decline, session_factory, settings, other, offer, key)

    assert exc.value.code == ErrorCode.RESOURCE_NOT_FOUND
    async with session_factory() as db:
        assert await find_receipt(db, other.id, key) is None


async def test_declined_attendee_rejoins_at_the_tail(session_factory, settings):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        a, b = await make_account(db), await make_account(db)
        offer = await make_offered(db, session, a, 1)
        await make_waiting(db, session, b, 2)
        session.last_queue_seq = 2
    await _run(psvc.decline, session_factory, settings, a, offer)

    rejoined = await booking.join_waitlist(
        session_factory,
        settings,
        account=a,
        session_id=session.id,
        request_id=uuid.uuid4(),
    )

    assert (rejoined.http_status, rejoined.result_code) == (201, "WAITLISTED")
    assert rejoined.participation_id != offer.id
    assert rejoined.payload["queue_position"] == 1  # b now holds the offer


# ---- Leave ----


async def test_leave_withdraws_and_promotes_nobody(session_factory, settings):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=2)  # one seat stays free
        h, a, b = [await make_account(db) for _ in range(3)]
        await make_held(db, session, h)
        entry = await make_waiting(db, session, a, 1)
        behind = await make_waiting(db, session, b, 2)
    before = await session_version(session_factory, session.id)

    outcome = await _run(psvc.leave, session_factory, settings, a, entry)

    assert (outcome.http_status, outcome.payload["end_reason"]) == (
        200,
        EndReason.WITHDRAWN,
    )
    assert _reasons(outcome) == ["cancelled"]
    assert (await latest_history(session_factory, entry.id)).actor_id == a.id
    assert await session_version(session_factory, session.id) == before + 1
    (behind,) = await reload(session_factory, [behind])
    assert behind.state == S.WAITLISTED
    assert await _position(session_factory, b, behind) == 1


async def test_leave_and_rejoin_goes_to_the_tail(session_factory, settings):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        h, a, b = [await make_account(db) for _ in range(3)]
        await make_held(db, session, h)
        entry = await make_waiting(db, session, a, 1)
        await make_waiting(db, session, b, 2)
        session.last_queue_seq = 2
    await _run(psvc.leave, session_factory, settings, a, entry)

    rejoined = await booking.join_waitlist(
        session_factory,
        settings,
        account=a,
        session_id=session.id,
        request_id=uuid.uuid4(),
    )

    assert rejoined.payload["queue_position"] == 2
    mine = await psvc.my_participation(session_factory, a, session.id)
    assert (mine.id, mine.state) == (rejoined.participation_id, S.WAITLISTED)
    old = await psvc.get_participation(session_factory, a, entry.id)
    assert (old.state, old.end_reason, old.historical) == (
        S.CANCELLED,
        "WITHDRAWN",
        True,
    )


@pytest.mark.parametrize("window", ["closed", "started"])
async def test_leave_after_close_meets_the_closed_entry(
    session_factory, settings, window
):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1, window=window)
        a = await make_account(db)
        entry = await make_waiting(db, session, a, 1)
    before = await session_version(session_factory, session.id)
    key = uuid.uuid4()

    outcome = await _run(psvc.leave, session_factory, settings, a, entry, key)

    assert (outcome.http_status, outcome.result_code) == (409, "REGISTRATION_CLOSED")
    assert await session_version(session_factory, session.id) == before + 1
    (entry,) = await reload(session_factory, [entry])
    assert (entry.state, entry.end_reason) == (
        S.CANCELLED,
        EndReason.REGISTRATION_CLOSED,
    )
    assert (await latest_history(session_factory, entry.id)).actor_id is None
    async with session_factory() as db:
        assert (await find_receipt(db, a.id, key)).http_status == 409


# ---- Shared by decline and leave ----


DECLINE = (psvc.decline, lambda db, s, a: make_offered(db, s, a, 1))
LEAVE = (psvc.leave, lambda db, s, a: make_waiting(db, s, a, 1))


@pytest.mark.parametrize("command,make", [DECLINE, LEAVE], ids=["decline", "leave"])
async def test_repeat_returns_the_unchanged_result(
    session_factory, settings, command, make
):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        a = await make_account(db)
        p = await make(db, session, a)
    key = uuid.uuid4()
    first = await _run(command, session_factory, settings, a, p, key)
    version = await session_version(session_factory, session.id)

    again = await _run(command, session_factory, settings, a, p)
    replayed = await _run(command, session_factory, settings, a, p, key)

    assert (again.http_status, again.result_code, again.changes) == (
        200,
        "CANCELLED",
        [],
    )
    assert await session_version(session_factory, session.id) == version
    assert replayed.replayed and replayed.payload == first.payload


@pytest.mark.parametrize(
    "command,make",
    [
        (psvc.decline, make_held),
        (psvc.decline, make_confirmed),
        (psvc.decline, lambda db, s, a: make_waiting(db, s, a, 1)),
        (psvc.leave, make_held),
        (psvc.leave, make_confirmed),
        (psvc.leave, lambda db, s, a: make_offered(db, s, a, 1)),
    ],
    ids=[
        "decline-held",
        "decline-confirmed",
        "decline-waiting",
        "leave-held",
        "leave-confirmed",
        "leave-offered",
    ],
)
async def test_wrong_state_is_409_and_changes_nothing(
    session_factory, settings, command, make
):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        a = await make_account(db)
        p = await make(db, session, a)
    before = await session_version(session_factory, session.id)

    outcome = await _run(command, session_factory, settings, a, p)

    assert (outcome.http_status, outcome.result_code) == (
        409,
        "INVALID_STATE_TRANSITION",
    )
    assert await session_version(session_factory, session.id) == before
    (after,) = await reload(session_factory, [p])
    assert (after.state, after.deadline_at) == (p.state, p.deadline_at)


async def _cancel_session(session_factory, settings, organizer, event, session, p):
    await ssvc.cancel_session(session_factory, organizer, session.id, "Room flooded")


async def _cancel_event(session_factory, settings, organizer, event, session, p):
    await esvc.cancel_event(session_factory, organizer, event.id, "Speaker ill")


async def _admin_cancel(session_factory, settings, organizer, event, session, p):
    async with session_factory.begin() as db:
        admin = await make_account(db, role=Role.ADMIN)
    await psvc.admin_cancel(
        session_factory,
        settings,
        admin=admin,
        participation_id=p.id,
        reason="Duplicate",
        request_id=uuid.uuid4(),
    )


@pytest.mark.parametrize(
    "end,code",
    [
        (_cancel_session, "SESSION_CANCELLED"),
        (_cancel_event, "EVENT_CANCELLED"),
        (_admin_cancel, "INVALID_STATE_TRANSITION"),
    ],
    ids=["session-cancelled", "event-cancelled", "admin-cancelled"],
)
@pytest.mark.parametrize("command,make", [DECLINE, LEAVE], ids=["decline", "leave"])
async def test_attempt_ended_another_way_is_409(
    session_factory, settings, command, make, end, code
):
    async with session_factory.begin() as db:
        organizer, event, session = await make_scope(db, capacity=1)
        a = await make_account(db)
        p = await make(db, session, a)
    await end(session_factory, settings, organizer, event, session, p)

    outcome = await _run(command, session_factory, settings, a, p)

    assert (outcome.http_status, outcome.result_code) == (409, code)


# ---- Cancel and administrator cancel follow the same repeat rule ----


async def test_cancel_after_session_cancellation_is_409(session_factory, settings):
    async with session_factory.begin() as db:
        organizer, _, session = await make_scope(db, capacity=1)
        a = await make_account(db)
        hold = await make_held(db, session, a)
    await ssvc.cancel_session(session_factory, organizer, session.id, "Room flooded")

    outcome = await _run(psvc.cancel, session_factory, settings, a, hold)

    assert outcome.result_code == "SESSION_CANCELLED"


async def test_cancel_of_a_withdrawn_entry_is_409(session_factory, settings):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        a = await make_account(db)
        entry = await make_waiting(db, session, a, 1)
    await _run(psvc.leave, session_factory, settings, a, entry)

    outcome = await _run(psvc.cancel, session_factory, settings, a, entry)

    assert outcome.result_code == "INVALID_STATE_TRANSITION"


async def test_cancel_after_the_start_is_409(session_factory, settings):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1, window="started")
        a = await make_account(db)
        confirmed = await make_confirmed(db, session, a)

    outcome = await _run(psvc.cancel, session_factory, settings, a, confirmed)

    assert outcome.result_code == "SESSION_STARTED"


async def test_admin_cancel_repeats_but_does_not_take_over_a_user_cancel(
    session_factory, settings
):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=2)
        admin = await make_account(db, role=Role.ADMIN)
        a, b = await make_account(db), await make_account(db)
        mine = await make_held(db, session, a)
        theirs = await make_held(db, session, b)

    async def admin_cancel(p):
        return await psvc.admin_cancel(
            session_factory,
            settings,
            admin=admin,
            participation_id=p.id,
            reason="Duplicate",
            request_id=uuid.uuid4(),
        )

    first, again = await admin_cancel(mine), await admin_cancel(mine)
    await _run(psvc.cancel, session_factory, settings, b, theirs)

    assert (first.result_code, again.result_code, again.changes) == (
        "CANCELLED",
        "CANCELLED",
        [],
    )
    assert (await admin_cancel(theirs)).result_code == "INVALID_STATE_TRANSITION"
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(ManagementAction)) == 1


# ---- Over HTTP ----


async def _offer_over_http(client, make_user):
    """Capacity 1: the holder cancels, so ``waiter`` gets an offer and
    ``behind`` stays first in the queue."""
    _, org = await make_user(Role.ORGANIZER)
    _, sid = await published_session(client, org, capacity=1)
    _, holder = await make_user()
    _, waiter = await make_user()
    _, behind = await make_user()
    held = (
        await client.post(f"/api/v1/sessions/{sid}/book", headers=holder | idem())
    ).json()["data"]
    for h in (waiter, behind):
        await client.post(f"/api/v1/sessions/{sid}/waitlist", headers=h | idem())
    await client.post(
        f"/api/v1/participations/{held['id']}/cancel", headers=holder | idem()
    )
    return sid, waiter, behind


async def _mine(client, sid, h) -> dict:
    return (
        await client.get(f"/api/v1/sessions/{sid}/my-participation", headers=h)
    ).json()["data"]


async def test_decline_over_http(client, make_user):
    sid, waiter, behind = await _offer_over_http(client, make_user)
    offer = await _mine(client, sid, waiter)
    key = idem()

    r = await client.post(
        f"/api/v1/participations/{offer['id']}/decline", headers=waiter | key
    )

    assert r.status_code == 200
    body = r.json()
    assert (body["meta"]["result"], body["data"]["end_reason"]) == (
        "CANCELLED",
        "OFFER_DECLINED",
    )
    assert body["meta"]["session_version"] == body["data"]["version"]
    assert (await _mine(client, sid, behind))["state"] == "OFFERED"
    stored = await client.get(
        f"/api/v1/requests/{key['Idempotency-Key']}", headers=waiter
    )
    assert stored.json()["data"] == body["data"]


async def test_leave_and_rejoin_over_http(client, make_user):
    sid, _, behind = await _offer_over_http(client, make_user)
    _, last = await make_user()
    await client.post(f"/api/v1/sessions/{sid}/waitlist", headers=last | idem())
    entry = await _mine(client, sid, behind)

    r = await client.post(
        f"/api/v1/participations/{entry['id']}/leave", headers=behind | idem()
    )
    assert (r.status_code, r.json()["data"]["end_reason"]) == (200, "WITHDRAWN")
    assert (await _mine(client, sid, last))["queue_position"] == 1

    r = await client.post(f"/api/v1/sessions/{sid}/waitlist", headers=behind | idem())
    assert (r.status_code, r.json()["data"]["queue_position"]) == (201, 2)


@pytest.mark.parametrize("command", ["decline", "leave"])
async def test_http_validation(client, make_user, command):
    sid, waiter, _ = await _offer_over_http(client, make_user)
    url = f"/api/v1/participations/{(await _mine(client, sid, waiter))['id']}/{command}"

    assert (await client.post(url, headers=waiter)).status_code == 422
    assert (await client.post(url, headers=idem())).status_code == 401
    assert (
        await client.post(url, headers=waiter | idem(), json={"x": 1})
    ).status_code == 422
