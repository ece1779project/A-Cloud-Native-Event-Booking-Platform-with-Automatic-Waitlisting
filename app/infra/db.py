from datetime import datetime

from sqlalchemy import MetaData, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings

# Predictable names for every index and constraint, so Alembic diffs stay stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,  # detect connections dropped while idle in the pool
        connect_args={
            "server_settings": {
                "timezone": "UTC",
                "lock_timeout": settings.db_lock_timeout,
                "statement_timeout": settings.db_statement_timeout,
                "idle_in_transaction_session_timeout": settings.db_idle_in_transaction_timeout,
            }
        },
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def db_now(db: AsyncSession) -> datetime:
    """Database wall clock, read after all blocking locks (DATABASE_DESIGN Section 4).

    Unlike now(), clock_timestamp() advances during the transaction, so a request
    that waited on a lock past a deadline is judged at the time it actually runs.
    """
    return await db.scalar(select(func.clock_timestamp()))


# PostgreSQL SQLSTATE codes the application reacts to.
LOCK_NOT_AVAILABLE = "55P03"  # lock_timeout expired
QUERY_CANCELED = "57014"  # statement_timeout expired
UNIQUE_VIOLATION = "23505"
CHECK_VIOLATION = "23514"


def sqlstate(exc: BaseException) -> str | None:
    """Extract the PostgreSQL SQLSTATE from a SQLAlchemy/asyncpg exception."""
    orig = getattr(exc, "orig", exc)
    for candidate in (orig, getattr(orig, "__cause__", None)):
        code = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if code:
            return code
    return None


def is_retryable_timeout(exc: BaseException) -> bool:
    """True for lock or statement timeouts, which map to a retryable 503."""
    return sqlstate(exc) in (LOCK_NOT_AVAILABLE, QUERY_CANCELED)
