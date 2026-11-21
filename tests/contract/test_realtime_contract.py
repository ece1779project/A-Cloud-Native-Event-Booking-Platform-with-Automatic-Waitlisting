"""WebSocket wire messages and Redis names fixed by TECHNICAL_DESIGN 10."""

import json
import uuid
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from app.core.redis_keys import (
    account_channel,
    login_rate_limit,
    management_channel,
    pubsub_pattern,
    registration_write_rate_limit,
    session_channel,
    websocket_ticket,
)
from app.schemas.realtime import ServerMessage

FIXTURES = Path(__file__).parent / "fixtures"
MESSAGE_ADAPTER = TypeAdapter(ServerMessage)


@pytest.mark.parametrize("fixture", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.stem)
def test_realtime_message_fixture(fixture: Path):
    message = MESSAGE_ADAPTER.validate_json(fixture.read_text())
    assert json.loads(message.model_dump_json()) == json.loads(fixture.read_text())


def test_all_server_message_types_have_contract_fixtures():
    assert {path.stem for path in FIXTURES.glob("*.json")} == {
        "auth.revoked",
        "connection.ready",
        "management.changed",
        "participation.changed",
        "session.changed",
        "subscription.ack",
        "system.resync_required",
    }


def test_redis_names_match_the_design():
    account_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    session_id = uuid.UUID("22222222-2222-4222-8222-222222222222")
    event_id = uuid.UUID("33333333-3333-4333-8333-333333333333")

    assert (
        registration_write_rate_limit(account_id, 123)
        == "seatflow:v1:rl:write:11111111-1111-4111-8111-111111111111:123"
    )
    assert login_rate_limit("2001:db8::1", 123) == "seatflow:v1:rl:login:2001:db8::1:123"
    assert websocket_ticket("abc123") == "seatflow:v1:ws:ticket:abc123"
    assert (
        session_channel(session_id)
        == "seatflow:v1:ps:session:22222222-2222-4222-8222-222222222222"
    )
    assert (
        account_channel(account_id)
        == "seatflow:v1:ps:account:11111111-1111-4111-8111-111111111111"
    )
    assert (
        management_channel(event_id)
        == "seatflow:v1:ps:event:33333333-3333-4333-8333-333333333333:management"
    )
    assert pubsub_pattern() == "seatflow:v1:ps:*"
