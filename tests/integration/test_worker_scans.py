"""Worker scans against PostgreSQL: candidate queries, one pass of each scan,
and the invariant scan."""

import logging

import pytest
from prometheus_client import REGISTRY

from app.domain.enums import PublicationStatus
from app.workers.closure import ClosureScan
from app.workers.expiry import ExpiryScan
from app.workers.invariant import InvariantScan
from app.workers.promotion import PromotionScan
from app.workers.runner import run_scan_once
from tests.factories import (
    make_account,
    make_confirmed,
    make_event,
    make_held,
    make_offered,
    make_scope,
    make_session,
    make_waiting,
)

VALIDITY = 600
invariant_holds = pytest.mark.usefixtures("capacity_invariant")


async def _session(db, *, capacity=2, window="open", status=None):
    event = await make_event(db, await make_account(db))
    return await make_session(
        db,
        event,
        capacity=capacity,
        window=window,
        status=status or PublicationStatus.PUBLISHED,
    )


async def _new(db):
    return await make_account(db)


@invariant_holds
async def test_expiry_candidates_and_backlog_gauges(session_factory):
    async with session_factory.begin() as db:
        due_hold = await _session(db)
        await make_held(db, due_hold, await _new(db), overdue=True)
        due_offer = await _session(db)
        await make_offered(db, due_offer, await _new(db), 1, overdue=True)
        await make_offered(db, due_offer, await _new(db), 2, overdue=True)
        not_due = await _session(db)
        await make_held(db, not_due, await _new(db))

    scan = ExpiryScan(session_factory, VALIDITY)
    assert set(await scan.find_candidates(10)) == {due_hold.id, due_offer.id}
    assert len(await scan.find_candidates(1)) == 1
    assert REGISTRY.get_sample_value("worker_overdue_items") == 3
    assert REGISTRY.get_sample_value("worker_oldest_overdue_seconds") >= 60


@invariant_holds
async def test_closure_candidates(session_factory):
    async with session_factory.begin() as db:
        closed = await _session(db, window="closed")
        await make_waiting(db, closed, await _new(db), 1)
        closed_done = await _session(db, window="closed")
        await make_confirmed(db, closed_done, await _new(db))
        still_open = await _session(db)
        await make_waiting(db, still_open, await _new(db), 1)
        draft = await _session(db, window="closed", status=PublicationStatus.DRAFT)
        await make_waiting(db, draft, await _new(db), 1)

    assert await ClosureScan(session_factory).find_candidates(10) == [closed.id]


@invariant_holds
async def test_promotion_candidates(session_factory):
    async with session_factory.begin() as db:
        free = await _session(db, capacity=2)
        await make_held(db, free, await _new(db))
        await make_waiting(db, free, await _new(db), 1)
        full = await _session(db, capacity=1)
        await make_confirmed(db, full, await _new(db))
        await make_waiting(db, full, await _new(db), 1)
        no_waiter = await _session(db)
        closed = await _session(db, window="closed")
        await make_waiting(db, closed, await _new(db), 1)

    scan = PromotionScan(session_factory, VALIDITY)
    assert await scan.find_candidates(10) == [free.id]
    assert no_waiter.id not in await scan.find_candidates(10)


@invariant_holds
async def test_one_pass_of_each_scan_clears_a_backlog(session_factory):
    async with session_factory.begin() as db:
        expiring = await _session(db, capacity=1)
        await make_held(db, expiring, await _new(db), overdue=True)
        await make_waiting(db, expiring, await _new(db), 1)
        closing = await _session(db, window="closed")
        await make_waiting(db, closing, await _new(db), 1)
        promoting = await _session(db, capacity=1)
        await make_waiting(db, promoting, await _new(db), 1)

    scans = [
        ExpiryScan(session_factory, VALIDITY),
        ClosureScan(session_factory),
        PromotionScan(session_factory, VALIDITY),
    ]
    published = []

    async def publish(change):
        published.append(change)

    for scan in scans:
        await run_scan_once(scan, batch_size=10, publish=publish)

    assert [(c.session_id, c.cause) for c in published] == [
        (expiring.id, "expiration"),
        (closing.id, "registration_closed"),
        (promoting.id, "promotion"),
    ]
    for scan in scans:
        assert await scan.find_candidates(10) == []


async def test_invariant_scan_reports_a_violation(session_factory, caplog):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        await make_held(db, session, await _new(db))
        await make_held(db, session, await _new(db))  # factories skip the check

    with caplog.at_level(logging.ERROR, logger="app.workers.invariant"):
        await run_scan_once(
            InvariantScan(session_factory), batch_size=10, publish=_ignore
        )

    assert REGISTRY.get_sample_value("capacity_violations") == 1
    assert str(session.id) in caplog.text


async def _ignore(change):
    pass
