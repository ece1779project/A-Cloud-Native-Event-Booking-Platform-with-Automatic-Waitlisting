"""The scan loop with fake scans: no database."""

import asyncio
import time
import uuid
from dataclasses import dataclass, field

from prometheus_client import REGISTRY

from app.schemas.realtime import ChangeCause, SessionChange
from app.workers.runner import run_scan_loop, run_scan_once


def _change() -> SessionChange:
    return SessionChange(
        event_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        version=1,
        cause=ChangeCause.EXPIRATION,
    )


@dataclass
class FakeScan:
    """Candidates are callables: each returns a change or None, or raises."""

    candidates: list = field(default_factory=list)
    query_error: Exception | None = None
    name: str = field(default_factory=lambda: f"fake-{uuid.uuid4().hex[:8]}")
    queries: int = 0

    async def find_candidates(self, limit):
        self.queries += 1
        if self.query_error is not None:
            raise self.query_error
        return self.candidates[:limit]

    async def process(self, candidate):
        return candidate()


def _sample(metric: str, **labels) -> float | None:
    return REGISTRY.get_sample_value(metric, labels)


def _fail():
    raise RuntimeError("boom")


async def test_a_failing_candidate_is_counted_and_skipped():
    changed = _change()
    published = []

    async def publish(change):
        published.append(change)

    scan = FakeScan(candidates=[_fail, lambda: changed, lambda: None])
    await run_scan_once(scan, batch_size=10, publish=publish)

    assert published == [changed]  # once per non-None result
    for outcome in ("error", "changed", "noop"):
        assert _sample("worker_candidates_total", scan=scan.name, outcome=outcome) == 1


async def test_a_publish_failure_does_not_stop_the_pass():
    async def publish(change):
        raise ConnectionError("redis down")

    scan = FakeScan(candidates=[_change, _change])
    await run_scan_once(scan, batch_size=10, publish=publish)

    assert _sample("worker_candidates_total", scan=scan.name, outcome="changed") == 2


async def test_an_empty_pass_sets_the_last_scan_gauge():
    scan = FakeScan()
    before = time.time()
    await run_scan_once(scan, batch_size=10, publish=_ignore)
    assert _sample("worker_last_scan_timestamp_seconds", scan=scan.name) >= before


async def test_query_failure_backs_off_and_leaves_the_gauge():
    scan = FakeScan(query_error=ConnectionError("database down"))
    stop = asyncio.Event()
    loop = asyncio.create_task(
        run_scan_loop(scan, interval=0.01, batch_size=10, stop=stop, max_backoff=0.08)
    )
    await asyncio.sleep(0.3)
    stop.set()
    await loop

    # Delays of 0.02, 0.04, 0.08, 0.08, ... allow only a handful of retries;
    # without backoff the 0.01 s interval would allow about 30.
    assert 2 <= scan.queries <= 8
    assert _sample("worker_last_scan_timestamp_seconds", scan=scan.name) is None


async def test_the_loop_stops_promptly_during_its_wait():
    scan = FakeScan()
    stop = asyncio.Event()
    loop = asyncio.create_task(
        run_scan_loop(scan, interval=10, batch_size=10, stop=stop)
    )
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(loop, timeout=0.5)
    assert scan.queries == 1


async def test_stop_ends_the_pass_before_the_next_candidate():
    stop = asyncio.Event()
    seen = []

    def first():
        seen.append(1)
        stop.set()

    scan = FakeScan(candidates=[first, lambda: seen.append(2)])
    await run_scan_once(scan, batch_size=10, publish=_ignore, stop=stop)
    assert seen == [1]


async def _ignore(change):
    pass
