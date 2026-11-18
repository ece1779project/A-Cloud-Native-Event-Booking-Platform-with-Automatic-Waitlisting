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
from app.workers.runner import publish_nothing, run_scan_once
from tests.factories import (
    make_account,
    make_confirmed,
    make_held,
    make_offered,
    make_scope,
    make_waiting,
)
from tests.helpers import OFFER_VALIDITY

invariant_holds = pytest.mark.usefixtures("capacity_invariant")


@invariant_holds
async def test_expiry_candidates_and_backlog_gauges(session_factory):
    async with session_factory.begin() as db:
        _, _, due_hold = await make_scope(db)
        await make_held(db, due_hold, await make_account(db), overdue=True)
        _, _, due_offer = await make_scope(db)
        await make_offered(db, due_offer, await make_account(db), 1, overdue=True)
        await make_offered(db, due_offer, await make_account(db), 2, overdue=True)
        _, _, not_due = await make_scope(db)
        await make_held(db, not_due, await make_account(db))

    scan = ExpiryScan(session_factory, OFFER_VALIDITY)
    assert set(await scan.find_candidates(10)) == {due_hold.id, due_offer.id}
    assert len(await scan.find_candidates(1)) == 1
    assert REGISTRY.get_sample_value("worker_overdue_items") == 3
    assert REGISTRY.get_sample_value("worker_oldest_overdue_seconds") >= 60


@invariant_holds
async def test_closure_candidates(session_factory):
    async with session_factory.begin() as db:
        _, _, closed = await make_scope(db, window="closed")
        await make_waiting(db, closed, await make_account(db), 1)
        _, _, closed_done = await make_scope(db, window="closed")
        await make_confirmed(db, closed_done, await make_account(db))
        _, _, still_open = await make_scope(db)
        await make_waiting(db, still_open, await make_account(db), 1)
        _, _, draft = await make_scope(
            db, window="closed", status=PublicationStatus.DRAFT
        )
        await make_waiting(db, draft, await make_account(db), 1)

    assert await ClosureScan(session_factory).find_candidates(10) == [closed.id]


@invariant_holds
async def test_promotion_candidates(session_factory):
    async with session_factory.begin() as db:
        _, _, free = await make_scope(db, capacity=2)
        await make_held(db, free, await make_account(db))
        await make_waiting(db, free, await make_account(db), 1)
        _, _, full = await make_scope(db, capacity=1)
        await make_confirmed(db, full, await make_account(db))
        await make_waiting(db, full, await make_account(db), 1)
        await make_scope(db)  # free seats but no waiter
        _, _, closed = await make_scope(db, window="closed")
        await make_waiting(db, closed, await make_account(db), 1)

    scan = PromotionScan(session_factory, OFFER_VALIDITY)
    assert await scan.find_candidates(10) == [free.id]


@invariant_holds
async def test_one_pass_of_each_scan_clears_a_backlog(session_factory):
    async with session_factory.begin() as db:
        _, _, expiring = await make_scope(db, capacity=1)
        await make_held(db, expiring, await make_account(db), overdue=True)
        await make_waiting(db, expiring, await make_account(db), 1)
        _, _, closing = await make_scope(db, window="closed")
        await make_waiting(db, closing, await make_account(db), 1)
        _, _, promoting = await make_scope(db, capacity=1)
        await make_waiting(db, promoting, await make_account(db), 1)

    scans = [
        ExpiryScan(session_factory, OFFER_VALIDITY),
        ClosureScan(session_factory),
        PromotionScan(session_factory, OFFER_VALIDITY),
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
        await make_held(db, session, await make_account(db))
        await make_held(db, session, await make_account(db))  # factories skip the check

    with caplog.at_level(logging.ERROR, logger="app.workers.invariant"):
        await run_scan_once(
            InvariantScan(session_factory), batch_size=10, publish=publish_nothing
        )

    assert REGISTRY.get_sample_value("capacity_violations") == 1
    assert str(session.id) in caplog.text
