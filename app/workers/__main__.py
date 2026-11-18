"""Worker process entry point: ``python -m app.workers``.

Starts every scan loop, serves health and metrics on ``WORKER_METRICS_PORT``
(the server is still to be added, see the TODO in ``main``), and handles
graceful shutdown.

On SIGTERM or SIGINT the loops stop taking candidates, the transactions in
flight finish, and the process exits within ``SHUTDOWN_GRACE_SECONDS``. The
loops catch their own errors and back off, so they end only on that signal.

Design: TECHNICAL_DESIGN.md Sections 11 and 13.3.
"""

import asyncio
import logging
import signal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.db.engine import create_engine, create_session_factory
from app.workers.closure import ClosureScan
from app.workers.expiry import ExpiryScan
from app.workers.invariant import InvariantScan
from app.workers.promotion import PromotionScan
from app.workers.runner import Scan, run_scan_loop

log = logging.getLogger("app.workers")

SHUTDOWN_GRACE_SECONDS = 20


def build_scans(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> list[Scan]:
    validity = settings.offer_validity_seconds
    return [
        ExpiryScan(session_factory, validity),
        ClosureScan(session_factory),
        PromotionScan(session_factory, validity),
        InvariantScan(session_factory),
    ]


async def main() -> None:
    settings = get_settings()
    # Switch to app.core.logging once it exists.
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    tasks = [
        asyncio.create_task(
            run_scan_loop(
                scan,
                interval=settings.worker_scan_interval_seconds,
                batch_size=settings.worker_scan_batch_size,
                stop=stop,
            ),
            name=scan.name,
        )
        for scan in build_scans(session_factory, settings)
    ]
    # TODO(Yifeng): add the health and metrics server on
    # settings.worker_metrics_port here as one more task.
    log.info("worker started: %s", ", ".join(t.get_name() for t in tasks))

    try:
        await stop.wait()
        # Each loop finishes its current transaction and returns; any loop
        # still running after the grace period is cancelled.
        await asyncio.wait_for(asyncio.gather(*tasks), SHUTDOWN_GRACE_SECONDS)
    except TimeoutError:
        log.warning("scan loops cancelled after %ss", SHUTDOWN_GRACE_SECONDS)
    finally:
        await engine.dispose()
    log.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
