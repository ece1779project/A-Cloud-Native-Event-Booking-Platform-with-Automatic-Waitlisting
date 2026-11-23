"""One ``AsyncEngine`` and ``async_sessionmaker`` per process, with UTC
connections, pool bounds from settings, and the lock, statement and
idle-in-transaction timeouts.

Also the database clock (``db_now``) and SQLSTATE helpers used to map lock
and statement timeouts to ``503 LOCK_TIMEOUT``.

Design: TECHNICAL_DESIGN.md Sections 7.1 and 13.4; DATABASE_DESIGN.md Section 6.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.constants import (
    DB_IDLE_IN_TRANSACTION_TIMEOUT,
    DB_LOCK_TIMEOUT,
    DB_STATEMENT_TIMEOUT,
)


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,  # detect connections dropped while idle in the pool
        connect_args={
            "server_settings": {
                "timezone": "UTC",
                "lock_timeout": DB_LOCK_TIMEOUT,
                "statement_timeout": DB_STATEMENT_TIMEOUT,
                "idle_in_transaction_session_timeout": DB_IDLE_IN_TRANSACTION_TIMEOUT,
            }
        },
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def db_now(db: AsyncSession) -> datetime:
    """Database wall clock, read after all blocking locks (TECHNICAL_DESIGN 6.3).

    Unlike now(), clock_timestamp() advances during the transaction, so a request
    that waited on a lock past a deadline is judged at the time it actually runs.
    """
    return await db.scalar(select(func.clock_timestamp()))


# PostgreSQL SQLSTATE codes the application reacts to.
LOCK_NOT_AVAILABLE = "55P03"  # lock_timeout expired -> 503 LOCK_TIMEOUT
QUERY_CANCELED = "57014"  # statement_timeout expired
UNIQUE_VIOLATION = "23505"
CHECK_VIOLATION = "23514"


def sqlstate(exc: BaseException) -> str | None:
    """Extract the PostgreSQL SQLSTATE from a SQLAlchemy/asyncpg exception."""
    orig = getattr(exc, "orig", exc)
    for candidate in (orig, getattr(orig, "__cause__", None)):
        code = getattr(candidate, "sqlstate", None) or getattr(
            candidate, "pgcode", None
        )
        if code:
            return code
    return None


def is_retryable_timeout(exc: BaseException) -> bool:
    """True for lock or statement timeouts, which map to a retryable 503."""
    return sqlstate(exc) in (LOCK_NOT_AVAILABLE, QUERY_CANCELED)
