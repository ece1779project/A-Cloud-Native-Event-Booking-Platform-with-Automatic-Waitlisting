"""Row checks, unique indexes and triggers from both migrations."""

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, text, update
from sqlalchemy.exc import DBAPIError

from app.db.engine import CHECK_VIOLATION, UNIQUE_VIOLATION, sqlstate
from app.domain.transitions import ALLOWED_TRANSITIONS
from app.models.auth_session import AuthSession
from app.models.event_session import EventSession
from app.models.management_action import ManagementAction
from app.models.participation import Participation
from tests.factories import make_account, make_held, make_scope, make_waiting


async def _expect_sqlstate(session_factory, code: str, action) -> None:
    with pytest.raises(DBAPIError) as info:
        async with session_factory.begin() as db:
            await action(db)
    assert sqlstate(info.value) == code, info.value


async def test_email_is_unique_case_insensitively(session_factory):
    async with session_factory.begin() as db:
        await make_account(db, email="Alice@Example.com")

    async def dup(db):
        await make_account(db, email="alice@example.COM")

    await _expect_sqlstate(session_factory, UNIQUE_VIOLATION, dup)


async def test_one_active_attempt_per_account_and_session(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db)
        attendee = await make_account(db)
        held = await make_held(db, session, attendee)

    async def second(db):
        await make_waiting(db, session, attendee, queue_seq=1)

    await _expect_sqlstate(session_factory, UNIQUE_VIOLATION, second)

    # After the first attempt ends, a new attempt is allowed.
    async with session_factory.begin() as db:
        now = datetime.now(UTC)
        await db.execute(
            update(Participation)
            .where(Participation.id == held.id)
            .values(state="CANCELLED", ended_at=now, end_reason="USER_CANCELLED")
        )
        await make_waiting(db, session, attendee, queue_seq=1)


async def test_queue_seq_reserved_after_attempt_ends(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db)
        a, b = await make_account(db), await make_account(db)
        await make_waiting(db, session, a, queue_seq=1)

    async def reuse(db):
        await make_waiting(db, session, b, queue_seq=1)

    await _expect_sqlstate(session_factory, UNIQUE_VIOLATION, reuse)


@pytest.mark.parametrize(
    "values",
    [
        # HELD must not carry a queue number.
        {"state": "HELD", "queue_seq": 1, "alloc": True},
        # WAITLISTED needs a queue number and no allocation.
        {"state": "WAITLISTED", "queue_seq": None, "alloc": False},
        {"state": "WAITLISTED", "queue_seq": 1, "alloc": True},
        # CONFIRMED needs confirmed_at inside [allocated_at, deadline_at).
        {"state": "CONFIRMED", "queue_seq": None, "alloc": True, "confirmed": "late"},
        # Terminal rows need ended_at and end_reason.
        {"state": "EXPIRED", "queue_seq": None, "alloc": True},
        # Unknown end reason.
        {"state": "CANCELLED", "queue_seq": None, "alloc": True, "reason": "BOGUS"},
    ],
)
async def test_participation_row_checks(session_factory, values):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db)
        attendee = await make_account(db)

    async def insert(db):
        now = datetime.now(UTC)
        deadline = now + timedelta(minutes=5)
        row = Participation(
            session_id=session.id,
            account_id=attendee.id,
            state=values["state"],
            queue_seq=values["queue_seq"],
            allocated_at=now if values["alloc"] else None,
            deadline_at=deadline if values["alloc"] else None,
        )
        if values.get("confirmed") == "late":
            row.confirmed_at = deadline
        if "reason" in values:
            row.ended_at, row.end_reason = now, values["reason"]
        db.add(row)
        await db.flush()

    await _expect_sqlstate(session_factory, CHECK_VIOLATION, insert)


async def test_session_time_order(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db)

    async def bad(db):
        await db.execute(
            update(EventSession)
            .where(EventSession.id == session.id)
            .values(registration_closes_at=EventSession.starts_at + timedelta(minutes=1))
        )

    await _expect_sqlstate(session_factory, CHECK_VIOLATION, bad)


async def test_management_action_needs_exactly_one_target(session_factory):
    async with session_factory.begin() as db:
        organizer, event, _ = await make_scope(db)

    async def two_targets(db):
        db.add(
            ManagementAction(
                actor_id=organizer.id,
                action="CANCEL_EVENT",
                reason="test",
                correlation_id=uuid.uuid4(),
                target_account_id=organizer.id,
                target_event_id=event.id,
            )
        )
        await db.flush()

    await _expect_sqlstate(session_factory, CHECK_VIOLATION, two_targets)


# --- trigger guards (second migration) -------------------------------------


async def test_history_tables_are_append_only(session_factory):
    async with session_factory.begin() as db:
        organizer, event, session = await make_scope(db)
        await make_held(db, session, organizer)  # also writes its history row
        db.add(
            ManagementAction(
                actor_id=organizer.id,
                action="CANCEL_EVENT",
                reason="test",
                correlation_id=uuid.uuid4(),
                target_event_id=event.id,
            )
        )

    for statement in (
        "UPDATE participation_changes SET reason_code = 'PROMOTED'",
        "DELETE FROM participation_changes",
        "UPDATE management_actions SET reason = 'edited'",
        "DELETE FROM management_actions",
    ):

        async def mutate(db, statement=statement):
            await db.execute(text(statement))

        await _expect_sqlstate(session_factory, "42501", mutate)


@pytest.mark.parametrize(
    ("start", "change"),
    [
        ("WAITLISTED", {"state": "CONFIRMED"}),  # must be offered first
        ("HELD", {"state": "OFFERED"}),
        ("CANCELLED", {"state": "HELD"}),  # terminal
        ("HELD", {"deadline_at": "+1m"}),  # allocation is immutable
        ("HELD", {"queue_seq": 7}),  # queue number is immutable
    ],
)
async def test_participation_transition_guard(session_factory, start, change):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db)
        attendee = await make_account(db)
        if start == "WAITLISTED":
            p = await make_waiting(db, session, attendee, queue_seq=1)
        else:
            p = await make_held(db, session, attendee)
            if start == "CANCELLED":
                await db.execute(
                    update(Participation)
                    .where(Participation.id == p.id)
                    .values(
                        state="CANCELLED",
                        ended_at=datetime.now(UTC),
                        end_reason="USER_CANCELLED",
                    )
                )

    values = dict(change)
    if values.get("deadline_at") == "+1m":
        values["deadline_at"] = Participation.deadline_at + timedelta(minutes=1)

    async def mutate(db):
        await db.execute(
            update(Participation).where(Participation.id == p.id).values(**values)
        )

    await _expect_sqlstate(session_factory, CHECK_VIOLATION, mutate)


async def test_legal_transition_is_allowed(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db)
        attendee = await make_account(db)
        p = await make_held(db, session, attendee)
    async with session_factory.begin() as db:
        await db.execute(
            update(Participation)
            .where(Participation.id == p.id)
            .values(state="CONFIRMED", confirmed_at=Participation.allocated_at)
        )


async def test_trigger_transitions_match_domain(session_factory):
    """The transition list exists twice: in the trigger and in app.domain.
    Read the trigger as deployed and fail when the two differ."""
    async with session_factory.begin() as db:
        source = await db.scalar(
            text("SELECT prosrc FROM pg_proc WHERE proname = 'guard_participation_update'")
        )
    allowed_list = source.split("(OLD.state, NEW.state) NOT IN", 1)[1].split("THEN", 1)[0]
    in_trigger = set(re.findall(r"\('(\w+)', '(\w+)'\)", allowed_list))

    assert in_trigger == {(src.value, dst.value) for src, dst in ALLOWED_TRANSITIONS}


async def test_schedule_freezes_after_any_participation(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db)
        attendee = await make_account(db)

    shift = {
        "registration_opens_at": EventSession.registration_opens_at - timedelta(minutes=5)
    }
    # No participation yet: schedule edits are allowed.
    async with session_factory.begin() as db:
        await db.execute(
            update(EventSession).where(EventSession.id == session.id).values(**shift)
        )

    async with session_factory.begin() as db:
        p = await make_held(db, session, attendee)
        await db.execute(
            update(Participation)
            .where(Participation.id == p.id)
            .values(state="CANCELLED", ended_at=datetime.now(UTC), end_reason="USER_CANCELLED")
        )

    # A terminal attempt still freezes the schedule ...
    async def edit(db):
        await db.execute(
            update(EventSession).where(EventSession.id == session.id).values(**shift)
        )

    await _expect_sqlstate(session_factory, CHECK_VIOLATION, edit)

    # ... but capacity can still change.
    async with session_factory.begin() as db:
        await db.execute(
            update(EventSession).where(EventSession.id == session.id).values(capacity=5)
        )


# --- auth_sessions (DATABASE_DESIGN 2.5) --------------------------------------


async def _make_auth_session(db, account, *, token: bytes = b"t" * 32):
    row = AuthSession(
        account_id=account.id,
        family_id=uuid.uuid4(),
        refresh_token_hash=token,
        csrf_token_hash=b"c" * 32,
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(row)
    await db.flush()
    return row


async def test_auth_session_refresh_token_is_unique(session_factory):
    async with session_factory.begin() as db:
        account = await make_account(db)
        await _make_auth_session(db, account)

    async def dup(db):
        await _make_auth_session(db, account)

    await _expect_sqlstate(session_factory, UNIQUE_VIOLATION, dup)


async def test_auth_session_revocation_needs_reason(session_factory):
    async with session_factory.begin() as db:
        account = await make_account(db)
        row = await _make_auth_session(db, account)

    async def revoke_without_reason(db):
        await db.execute(
            update(AuthSession)
            .where(AuthSession.id == row.id)
            .values(revoked_at=datetime.now(UTC))
        )

    await _expect_sqlstate(session_factory, CHECK_VIOLATION, revoke_without_reason)


async def test_auth_session_rotation_and_revocation_stamp_once(session_factory):
    async with session_factory.begin() as db:
        account = await make_account(db)
        row = await _make_auth_session(db, account)

    # Stamping rotation and revocation once is allowed.
    async with session_factory.begin() as db:
        await db.execute(
            update(AuthSession)
            .where(AuthSession.id == row.id)
            .values(
                rotated_at=func.now(),
                last_used_at=func.now(),
                revoked_at=func.now(),
                revoke_reason="LOGOUT",
            )
        )

    for change in (
        {"rotated_at": func.now() + timedelta(seconds=1)},
        {"revoke_reason": "REUSE_DETECTED"},
        {"expires_at": AuthSession.expires_at + timedelta(days=1)},
        {"refresh_token_hash": b"x" * 32},
    ):

        async def mutate(db, change=change):
            await db.execute(
                update(AuthSession).where(AuthSession.id == row.id).values(**change)
            )

        await _expect_sqlstate(session_factory, CHECK_VIOLATION, mutate)
