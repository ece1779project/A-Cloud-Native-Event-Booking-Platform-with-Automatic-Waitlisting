"""PostgreSQL integration-test fixtures.

Set TEST_DATABASE_URL to a disposable database whose name contains "test", e.g.

    docker run -d --rm --name booking-test-db -p 55432:5432 \\
        -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=booking_test \\
        postgres:18
    TEST_DATABASE_URL=postgresql+asyncpg://test:test@127.0.0.1:55432/booking_test \\
        uv run pytest tests/db

The public schema is dropped and rebuilt with `alembic upgrade head` once per
run, and every table is truncated before each test. Without the variable these
tests are skipped.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.infra.db import create_engine, create_session_factory

ROOT = Path(__file__).resolve().parents[2]
TABLES = (
    "accounts, events, event_sessions, participations, "
    "participation_changes, request_results, management_actions"
)


async def _reset_schema(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    if "test" not in (make_url(url).database or ""):
        pytest.exit(f"refusing to wipe non-test database {make_url(url).database!r}")

    asyncio.run(_reset_schema(url))
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": url, "REDIS_URL": "redis://unused"},
        check=True,
        capture_output=True,
    )
    return url


@pytest_asyncio.fixture
async def engine(database_url):
    engine = create_engine(Settings(database_url=database_url, redis_url="redis://unused"))
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES}"))
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine):
    return create_session_factory(engine)
