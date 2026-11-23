"""The promotion latency report pairs each freed seat with the offer that
took it, by correlation ID."""

import uuid
from datetime import UTC, datetime, timedelta

from app.cli.promotion_report import format_report, percentile, promotion_latencies
from app.schemas.sessions import SessionPatch
from app.services import participations as psvc
from app.services import sessions as ssvc
from tests.factories import (
    make_account,
    make_held,
    make_offered,
    make_scope,
    make_waiting,
)
from tests.helpers import expire


async def _report(session_factory, **kw):
    async with session_factory() as db:
        return await promotion_latencies(db, **kw)


async def test_each_category_is_paired_and_the_rest_counted(session_factory, settings):
    async with session_factory.begin() as db:
        _, _, expiring = await make_scope(db, capacity=1)
        await make_held(db, expiring, await make_account(db), overdue=True)
        await make_waiting(db, expiring, await make_account(db), 1)

        _, _, cancelling = await make_scope(db, capacity=1)
        holder = await make_account(db)
        hold = await make_held(db, cancelling, holder)
        await make_waiting(db, cancelling, await make_account(db), 1)

        _, _, declining = await make_scope(db, capacity=1)
        offeree = await make_account(db)
        offer = await make_offered(
            db, declining, offeree, 1
        )  # its own offer is unpaired
        await make_waiting(db, declining, await make_account(db), 2)

        growing_owner, _, growing = await make_scope(db, capacity=1)
        await make_held(db, growing, await make_account(db))
        await make_waiting(db, growing, await make_account(db), 1)

    await expire(session_factory, expiring.id)
    for account, p, command in (
        (holder, hold, psvc.cancel),
        (offeree, offer, psvc.decline),
    ):
        await command(
            session_factory,
            settings,
            account=account,
            participation_id=p.id,
            request_id=uuid.uuid4(),
        )
    await ssvc.patch_session(
        session_factory, settings, growing_owner, growing.id, SessionPatch(capacity=2)
    )  # an offer with no release

    report = await _report(session_factory)

    assert len(report.seconds["expiration"]) == 1
    assert len(report.seconds["cancellation"]) == 2
    assert all(s >= 0 for values in report.seconds.values() for s in values)
    # The deadline passed a minute before the worker ran.
    assert report.seconds["expiration"][0] > 50
    assert (report.unpaired_offers, report.unpaired_releases) == (2, 0)
    assert "target_met=False" in format_report(report)


async def test_filters(session_factory):
    async with session_factory.begin() as db:
        _, _, session = await make_scope(db, capacity=1)
        await make_held(db, session, await make_account(db), overdue=True)
        await make_waiting(db, session, await make_account(db), 1)
        _, _, other = await make_scope(db, capacity=1)
        await make_held(db, other, await make_account(db), overdue=True)
        await make_waiting(db, other, await make_account(db), 1)
    await expire(session_factory, session.id)
    await expire(session_factory, other.id)

    only_one = await _report(session_factory, session_id=session.id)
    later = await _report(session_factory, since=datetime.now(UTC) + timedelta(hours=1))

    assert len(only_one.seconds["expiration"]) == 1
    assert later.seconds["expiration"] == []


def test_percentile_is_nearest_rank():
    values = [float(n) for n in range(1, 101)]
    assert (percentile(values, 0.5), percentile(values, 0.95)) == (50.0, 95.0)
