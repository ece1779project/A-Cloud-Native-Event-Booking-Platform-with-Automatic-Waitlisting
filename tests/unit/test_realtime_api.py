"""Realtime ticket route scope grants without external Redis or PostgreSQL."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import AccessContext, get_optional_access_context
from app.api.v1.realtime import router
from app.domain.enums import Role
from app.models.account import Account


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def set(self, name: str, value: str, *, ex: int, nx: bool):
        assert ex == 30 and nx
        self.values[name] = value
        return True


def _app() -> FastAPI:
    app = FastAPI()
    app.state.redis = FakeRedis()
    app.include_router(router)
    return app


def test_anonymous_ticket_has_public_scope_only():
    response = TestClient(_app()).post("/realtime/tickets")
    assert response.status_code == 201
    assert response.json()["data"]["scopes"] == ["public"]


def test_organizer_ticket_has_account_and_management_scopes():
    app = _app()
    account = Account(
        id=uuid.uuid4(),
        email="organizer@example.com",
        display_name="Organizer",
        password_hash="unused",
        role=Role.ORGANIZER,
        disabled_at=None,
    )

    async def organizer_access():
        return AccessContext(
            account=account,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

    app.dependency_overrides[get_optional_access_context] = organizer_access
    response = TestClient(app).post("/realtime/tickets")
    assert response.status_code == 201
    assert response.json()["data"]["scopes"] == [
        "public",
        "account",
        "management",
    ]
