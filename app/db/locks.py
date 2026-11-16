"""The only business-write lock entry point.

Locks the event, the session and the accounts in the documented order,
returns the locked rows and reads ``clock_timestamp()`` after the last
blocking lock. Every API and worker write that can affect participation,
capacity, queue order or session status goes through this module. Locking
these rows anywhere else fails review.

Order:

1. the parent event: FOR SHARE for a booking command, FOR UPDATE for a
   whole-event edit or cancellation;
2. its sessions FOR UPDATE, in ascending ID order;
3. the accounts the command depends on, in ascending ID order: FOR SHARE for a
   permission or eligibility check, FOR UPDATE for a role or disablement change.

Account-only changes use ``lock_account_scope``. Promotion locks each queue
head's account afterwards with ``lock_queue_head_account`` (shared only, which
cannot deadlock). Every lock re-reads the row (``populate_existing``) so
callers never act on stale identity-map state.

Design: TECHNICAL_DESIGN.md Section 7.1; DATABASE_DESIGN.md Section 4.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import db_now
from app.models.account import Account
from app.models.event import Event
from app.models.event_session import EventSession


class RowMissing(LookupError):
    def __init__(self, table: str, row_id: uuid.UUID) -> None:
        super().__init__(f"{table} row {row_id} does not exist")
        self.table = table
        self.row_id = row_id


@dataclass(frozen=True, slots=True)
class LockedScope:
    event: Event
    sessions: dict[uuid.UUID, EventSession]
    accounts: dict[uuid.UUID, Account]
    now: datetime  # clock_timestamp() read after the last lock

    @property
    def session(self) -> EventSession:
        """The only locked session; for single-session commands."""
        if len(self.sessions) != 1:
            raise ValueError(f"scope holds {len(self.sessions)} sessions, not one")
        return next(iter(self.sessions.values()))


@dataclass(frozen=True, slots=True)
class LockedAccounts:
    accounts: dict[uuid.UUID, Account]
    now: datetime


@dataclass(frozen=True, slots=True)
class LockedQueueHead:
    account: Account
    now: datetime  # fresh time after this lock (TECHNICAL_DESIGN 7.4 step 5)


async def lock_event_scope(
    db: AsyncSession,
    event_id: uuid.UUID,
    *,
    session_ids: Iterable[uuid.UUID] = (),
    all_sessions: bool = False,
    exclusive_event: bool = False,
    share_account_ids: Iterable[uuid.UUID] = (),
    exclusive_account_ids: Iterable[uuid.UUID] = (),
) -> LockedScope:
    """Lock an event, some or all of its sessions, then accounts, in design order.

    ``all_sessions`` requires ``exclusive_event``: only an exclusive event lock
    stops new sessions being inserted (their FK check needs a key-share lock).
    """
    if all_sessions and not exclusive_event:
        raise ValueError("all_sessions requires exclusive_event")

    stmt = select(Event).where(Event.id == event_id)
    stmt = stmt.with_for_update() if exclusive_event else stmt.with_for_update(read=True)
    event = await db.scalar(stmt.execution_options(populate_existing=True))
    if event is None:
        raise RowMissing("events", event_id)

    if all_sessions:
        ids = list(
            await db.scalars(
                select(EventSession.id).where(EventSession.event_id == event_id)
            )
        )
    else:
        ids = list(session_ids)

    sessions: dict[uuid.UUID, EventSession] = {}
    for session_id in sorted(set(ids)):
        locked = await db.scalar(
            select(EventSession)
            .where(EventSession.id == session_id, EventSession.event_id == event_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked is None:
            raise RowMissing("event_sessions", session_id)
        sessions[session_id] = locked

    accounts = await _lock_accounts(
        db, share_ids=share_account_ids, exclusive_ids=exclusive_account_ids
    )
    return LockedScope(
        event=event, sessions=sessions, accounts=accounts, now=await db_now(db)
    )


async def lock_session_scope(
    db: AsyncSession,
    session_id: uuid.UUID,
    *,
    share_account_ids: Iterable[uuid.UUID] = (),
    exclusive_account_ids: Iterable[uuid.UUID] = (),
) -> LockedScope:
    """Booking-command scope: event FOR SHARE, one session FOR UPDATE, accounts.

    The unlocked event_id lookup is safe because a session's event is immutable
    (enforced by trg_event_sessions_guard_update).
    """
    event_id = await db.scalar(
        select(EventSession.event_id).where(EventSession.id == session_id)
    )
    if event_id is None:
        raise RowMissing("event_sessions", session_id)
    return await lock_event_scope(
        db,
        event_id,
        session_ids=[session_id],
        share_account_ids=share_account_ids,
        exclusive_account_ids=exclusive_account_ids,
    )


async def lock_account_scope(
    db: AsyncSession,
    *,
    share_ids: Iterable[uuid.UUID] = (),
    exclusive_ids: Iterable[uuid.UUID] = (),
) -> LockedAccounts:
    """Account-only changes (role, disablement): no event or session locks."""
    accounts = await _lock_accounts(db, share_ids=share_ids, exclusive_ids=exclusive_ids)
    return LockedAccounts(accounts=accounts, now=await db_now(db))


async def lock_queue_head_account(
    db: AsyncSession, account_id: uuid.UUID
) -> LockedQueueHead:
    """Shared lock on a waitlist head's account during promotion.

    This is the one sanctioned out-of-order account lock: promotion runs after
    the caller's own locks and takes only shared account locks.
    """
    account = await db.scalar(
        select(Account)
        .where(Account.id == account_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if account is None:
        raise RowMissing("accounts", account_id)
    return LockedQueueHead(account=account, now=await db_now(db))


async def _lock_accounts(
    db: AsyncSession,
    *,
    share_ids: Iterable[uuid.UUID],
    exclusive_ids: Iterable[uuid.UUID],
) -> dict[uuid.UUID, Account]:
    """Lock accounts in ascending ID order. An ID in both sets is locked
    exclusively, because the mode must be chosen on first acquisition."""
    exclusive = set(exclusive_ids)
    accounts: dict[uuid.UUID, Account] = {}
    for account_id in sorted(exclusive | set(share_ids)):
        stmt = select(Account).where(Account.id == account_id)
        stmt = (
            stmt.with_for_update()
            if account_id in exclusive
            else stmt.with_for_update(read=True)
        )
        account = await db.scalar(stmt.execution_options(populate_existing=True))
        if account is None:
            raise RowMissing("accounts", account_id)
        accounts[account_id] = account
    return accounts
