"""Fixtures shared by every test layer.

The PostgreSQL fixtures live here, not in tests/integration/conftest.py,
because tests/integration and tests/concurrency both need them.

Set TEST_DATABASE_URL to a disposable database whose name contains "test", e.g.

    docker run -d --rm --name booking-test-db -p 55432:5432 \\
        -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=booking_test \\
        postgres:18
    TEST_DATABASE_URL=postgresql+asyncpg://test:test@127.0.0.1:55432/booking_test \\
        uv run pytest

The public schema is dropped and rebuilt with `alembic upgrade head` once per
run, and every table is truncated before each test. Without the variable the
database tests are skipped; tests/unit still runs.
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
from app.db.engine import create_engine, create_session_factory
from app.db.participation_store import capacity_violations

ROOT = Path(__file__).resolve().parents[1]
TABLES = (
    "accounts, auth_sessions, events, event_sessions, participations, "
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
    engine = create_engine(
        Settings(_env_file=None, database_url=database_url, redis_url="redis://unused")
    )
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES}"))
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine):
    return create_session_factory(engine)


# ---- Chapter 02: Ed25519 key pair generated per run, so tests need no .env ----
@pytest.fixture(scope="session")
def jwt_keys() -> tuple[str, str]:
    # Imported here so the rest of the suite runs before chapter 02 adds the dependency.
    from cryptography.hazmat.primitives import serialization as s
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    k = Ed25519PrivateKey.generate()
    private = k.private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()).decode()
    public = k.public_key().public_bytes(s.Encoding.PEM, s.PublicFormat.SubjectPublicKeyInfo).decode()
    return private, public


# ---- Chapters 03 and 09: settings shared by integration and concurrency tests ----
@pytest.fixture
def settings(database_url, jwt_keys) -> Settings:
    # Ignore .env, so local demo values (e.g. short validity) never reach the tests.
    return Settings(
        _env_file=None,
        database_url=database_url,
        redis_url="redis://unused",
        jwt_private_key=jwt_keys[0],
        jwt_public_key=jwt_keys[1],
    )


@pytest_asyncio.fixture
async def capacity_invariant(session_factory):
    """Opt in with ``pytestmark = pytest.mark.usefixtures("capacity_invariant")``:
    after the test, the capacity-invariant query must return no rows."""
    yield
    async with session_factory.begin() as db:
        assert await capacity_violations(db) == []
