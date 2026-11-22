"""Integration tests: services and API routes against a real PostgreSQL and
Redis. The PostgreSQL fixtures live in ``tests/conftest.py`` because the
concurrency tests share them; fixtures used only here, such as the HTTP
client, belong in this file.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx2 import ASGITransport, AsyncClient

from app.domain.enums import Role
from app.main import create_app
from app.models.account import Account

PASSWORD = "correct horse battery staple"


# ---- Chapter 03: app, HTTP client and logged-in users ----
# ``settings`` lives in tests/conftest.py so tests/concurrency can use it too.


@pytest.fixture
def app(settings, session_factory):
    app = create_app()
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.redis = None
    return app


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as c:
        yield c


@pytest_asyncio.fixture
async def make_user(session_factory, client):
    from app.core.security import hash_password  # implemented in chapter 02

    async def _make(role: Role = Role.ATTENDEE, *, disabled: bool = False):
        email = f"{uuid.uuid4().hex[:10]}@example.com"
        async with session_factory.begin() as db:
            acct = Account(email=email, display_name=role.value.title(),
                           password_hash=hash_password(PASSWORD), role=role.value)
            db.add(acct)
        r = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        token = r.json()["data"]["access_token"]
        return acct, {"Authorization": f"Bearer {token}"}

    return _make


def idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


# ---- Chapter 06: a valid session body and a published session ----


def session_body(*, capacity=2, opens=-1, closes=1, starts=2, ends=3) -> dict:
    now = datetime.now(UTC)
    h = lambda n: (now + timedelta(hours=n)).isoformat()
    return {"name": "Morning", "time_zone": "America/Toronto", "capacity": capacity,
            "registration_opens_at": h(opens), "registration_closes_at": h(closes),
            "starts_at": h(starts), "ends_at": h(ends)}


async def published_session(client, headers, **kw) -> tuple[str, str]:
    e = (await client.post("/api/v1/events", headers=headers,
                           json={"title": "Workshop", "description": "d", "location": "Toronto"})).json()["data"]
    s = (await client.post(f"/api/v1/events/{e['id']}/sessions", headers=headers,
                           json=session_body(**kw))).json()["data"]
    assert (await client.post(f"/api/v1/events/{e['id']}/publish", headers=headers)).status_code == 200
    return e["id"], s["id"]
