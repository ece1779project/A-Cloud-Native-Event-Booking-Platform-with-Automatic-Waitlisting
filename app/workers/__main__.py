"""Worker process entry point: ``python -m app.workers``.

Starts every scan loop, serves health and metrics on ``WORKER_METRICS_PORT``
and handles graceful shutdown.

On SIGTERM or SIGINT the loops stop taking candidates, the transactions in
flight finish, and the process exits within ``SHUTDOWN_GRACE_SECONDS``. The
loops catch their own errors and back off, so they end only on that signal.

Design: TECHNICAL_DESIGN.md Sections 11 and 13.3.
"""

import asyncio
import logging
import signal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from uvicorn import Config, Server

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.redis_client import create_redis
from app.db.engine import create_engine, create_session_factory
from app.realtime.publisher import publish_changes
from app.schemas.realtime import SessionChange
from app.workers.closure import ClosureScan
from app.workers.expiry import ExpiryScan
from app.workers.health import create_health_app
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
    configure_logging(
        service="worker",
        environment=settings.environment,
        level=settings.log_level,
    )
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    redis = create_redis(settings.redis_url)

    async def publish(change: SessionChange) -> None:
        await publish_changes(redis, [change])

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
                publish=publish,
            ),
            name=scan.name,
        )
        for scan in build_scans(session_factory, settings)
    ]
    health_server = Server(
        Config(
            create_health_app(engine),
            host="0.0.0.0",
            port=settings.worker_metrics_port,
            log_level=settings.log_level.lower(),
            access_log=False,
        )
    )
    health_task = asyncio.create_task(health_server.serve(), name="health")
    stop_task = asyncio.create_task(stop.wait(), name="signal")
    log.info(
        "worker started: scans=%s health_port=%s",
        ", ".join(t.get_name() for t in tasks),
        settings.worker_metrics_port,
    )

    try:
        done, _pending = await asyncio.wait(
            (stop_task, health_task), return_when=asyncio.FIRST_COMPLETED
        )
        if health_task in done:
            await health_task  # propagate a bind or server failure
            raise RuntimeError("worker health server stopped unexpectedly")

        health_server.should_exit = True
        # Each loop finishes its current transaction and returns; any loop
        # still running after the grace period is cancelled.
        await asyncio.wait_for(
            asyncio.gather(*tasks, health_task), SHUTDOWN_GRACE_SECONDS
        )
    except TimeoutError:
        log.warning("scan loops cancelled after %ss", SHUTDOWN_GRACE_SECONDS)
    finally:
        stop.set()
        health_server.should_exit = True
        stop_task.cancel()
        for task in (*tasks, health_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(stop_task, *tasks, health_task, return_exceptions=True)
        await redis.aclose()
        await engine.dispose()
    log.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
