"""The scan-loop scaffold shared by all scans: interval, bounded batch,
backoff on dependency failure, cancellation and per-scan metrics. A finished
pass records ``worker_last_scan_timestamp_seconds``.

A scan only selects candidates; each candidate is processed in its own
transaction by a service, so a stale candidate is a harmless no-op. The
runner knows nothing about the database: scans open their own transactions.

Design: TECHNICAL_DESIGN.md Sections 11 and 14.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from app.core.metrics import WORKER_CANDIDATES, WORKER_LAST_SCAN, WORKER_SCAN_DURATION
from app.db.engine import is_retryable_timeout
from app.schemas.realtime import SessionChange

log = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 30.0

Publish = Callable[[SessionChange], Awaitable[None]]


class Scan(Protocol):
    name: str

    async def find_candidates(self, limit: int) -> Sequence[Any]:
        """Read up to ``limit`` candidates in a short read-only transaction."""
        ...

    async def process(self, candidate: Any) -> SessionChange | None:
        """Handle one candidate in its own transaction."""
        ...


async def publish_nothing(change: SessionChange) -> None:
    """Default until the real-time publisher exists."""


async def run_scan_once(
    scan: Scan, *, batch_size: int, publish: Publish, stop: asyncio.Event | None = None
) -> None:
    """One pass: read candidates, then process them one at a time.

    A failing candidate is logged, counted and skipped. A failing candidate
    query propagates: it is a dependency failure, and the loop backs off.
    """
    started = time.monotonic()
    candidates = await scan.find_candidates(batch_size)
    for candidate in candidates:
        if stop is not None and stop.is_set():
            break  # finish the current transaction, take no new candidates
        await _process(scan, candidate, publish)
    WORKER_SCAN_DURATION.labels(scan=scan.name).observe(time.monotonic() - started)
    WORKER_LAST_SCAN.labels(scan=scan.name).set_to_current_time()


async def _process(scan: Scan, candidate: Any, publish: Publish) -> None:
    try:
        change = await scan.process(candidate)
    except Exception as exc:
        if is_retryable_timeout(exc):
            outcome = "busy"  # session lock held elsewhere; next pass retries
            log.warning("%s scan: candidate %s busy", scan.name, candidate)
        else:
            outcome = "error"
            log.exception("%s scan: candidate %s failed", scan.name, candidate)
        WORKER_CANDIDATES.labels(scan=scan.name, outcome=outcome).inc()
        return

    outcome = "noop" if change is None else "changed"
    WORKER_CANDIDATES.labels(scan=scan.name, outcome=outcome).inc()
    if change is not None:
        try:
            await publish(change)
        except Exception:
            # Publication is best effort; the change is already committed.
            log.exception("%s scan: publishing %s failed", scan.name, change)


async def run_scan_loop(
    scan: Scan,
    *,
    interval: float,
    batch_size: int,
    stop: asyncio.Event,
    publish: Publish = publish_nothing,
    max_backoff: float = MAX_BACKOFF_SECONDS,
) -> None:
    """Run passes every ``interval`` seconds until ``stop`` is set.

    After a dependency failure the delay doubles, up to ``max_backoff``, and
    the last-scan gauge keeps its previous value.
    """
    backoff = interval
    while not stop.is_set():
        started = time.monotonic()
        try:
            await run_scan_once(scan, batch_size=batch_size, publish=publish, stop=stop)
        except Exception:
            backoff = min(backoff * 2, max_backoff)
            log.exception("%s scan failed; retrying in %.1fs", scan.name, backoff)
            delay = backoff
        else:
            backoff = interval
            delay = interval - (time.monotonic() - started)
        await _sleep_unless_stopped(stop, delay)


async def _sleep_unless_stopped(stop: asyncio.Event, delay: float) -> None:
    if delay <= 0:
        return
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay)
    except TimeoutError:
        pass
