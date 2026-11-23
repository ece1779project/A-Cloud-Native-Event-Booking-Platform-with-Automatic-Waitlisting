"""Gateway channel parsing, ticket scopes, limits, and local indexes."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.redis_keys import session_channel
from app.realtime.gateway.connection import (
    ClientConnection,
    ConnectionRegistry,
    handle_connection,
)
from app.realtime.gateway.fanout import deliver
from app.realtime.gateway.subscriptions import (
    ChannelKind,
    ScopeViolation,
    SubscriptionLimitExceeded,
    SubscriptionState,
    apply_request,
    parse_channel,
)
from app.realtime.tickets import TicketGrant, issue_ticket
from app.schemas.realtime import SubscriptionRequest, TicketScope


class UnusedDatabase:
    """The tested policy paths finish before issuing a database query."""


class FakeWebSocket:
    def __init__(self):
        self.sent: list[str] = []
        self.closed: list[int] = []

    async def send_text(self, payload: str):
        self.sent.append(payload)

    async def close(self, code: int):
        self.closed.append(code)


class TicketRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def set(self, name: str, value: str, *, ex: int, nx: bool):
        assert ex == 30 and nx
        self.values[name] = value
        return True

    async def getdel(self, name: str):
        return self.values.pop(name, None)


class FakeSessionContext:
    async def __aenter__(self):
        return UnusedDatabase()

    async def __aexit__(self, *_args):
        return None


def test_channel_parser_accepts_only_canonical_contract_names():
    session_id = uuid.uuid4()
    event_id = uuid.uuid4()

    session = parse_channel(f"session:{session_id}")
    assert session is not None
    assert session.kind is ChannelKind.SESSION
    assert session.resource_id == session_id

    management = parse_channel(f"event:{event_id}:management")
    assert management is not None
    assert management.kind is ChannelKind.MANAGEMENT
    assert parse_channel("me:participations").kind is ChannelKind.ACCOUNT

    assert parse_channel(f"session:{str(session_id).upper()}") is None
    assert parse_channel("session:not-a-uuid") is None
    assert parse_channel(f"event:{event_id}:other") is None


async def test_account_channel_requires_account_ticket_scope():
    state = SubscriptionState(scopes=frozenset({TicketScope.PUBLIC}))
    request = SubscriptionRequest(
        type="subscribe",
        request_id=uuid.uuid4(),
        channels=("me:participations",),
    )

    with pytest.raises(ScopeViolation):
        await apply_request(
            UnusedDatabase(),
            state,
            request,
            account_id=None,  # type: ignore[arg-type]
        )
    assert state.channels == set()


async def test_account_channel_can_be_subscribed_and_unsubscribed():
    state = SubscriptionState(
        scopes=frozenset({TicketScope.PUBLIC, TicketScope.ACCOUNT})
    )
    request_id = uuid.uuid4()
    subscribe = SubscriptionRequest(
        type="subscribe",
        request_id=request_id,
        channels=("me:participations", "bad"),
    )
    ack = await apply_request(
        UnusedDatabase(),  # type: ignore[arg-type]
        state,
        subscribe,
        account_id=uuid.uuid4(),
    )
    assert ack.accepted == ("me:participations",)
    assert ack.rejected[0].code == "INVALID_CHANNEL"
    assert state.channels == {"me:participations"}

    unsubscribe = SubscriptionRequest(
        type="unsubscribe",
        request_id=request_id,
        channels=("me:participations",),
    )
    ack = await apply_request(
        UnusedDatabase(),  # type: ignore[arg-type]
        state,
        unsubscribe,
        account_id=uuid.uuid4(),
    )
    assert ack.accepted == ("me:participations",)
    assert state.channels == set()


async def test_public_channel_limit_closes_before_database_work():
    channels = tuple(f"session:{uuid.uuid4()}" for _ in range(51))
    state = SubscriptionState(scopes=frozenset({TicketScope.PUBLIC}))
    request = SubscriptionRequest(
        type="subscribe", request_id=uuid.uuid4(), channels=channels
    )
    with pytest.raises(SubscriptionLimitExceeded):
        await apply_request(
            UnusedDatabase(),
            state,
            request,
            account_id=None,  # type: ignore[arg-type]
        )


async def test_registry_updates_channel_and_account_indexes():
    account_id = uuid.uuid4()
    websocket = FakeWebSocket()
    connection = ClientConnection(
        websocket=websocket,  # type: ignore[arg-type]
        grant=TicketGrant(
            account_id=account_id,
            access_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            scopes=(TicketScope.PUBLIC, TicketScope.ACCOUNT),
        ),
    )
    registry = ConnectionRegistry()
    await registry.add(connection)

    previous: set[str] = set()
    connection.subscriptions.channels.add("me:participations")
    await registry.subscriptions_changed(connection, previous)
    assert await registry.for_channel("me:participations") == (connection,)
    assert await registry.for_account(account_id) == (connection,)

    await registry.remove(connection)
    assert await registry.for_channel("me:participations") == ()
    assert await registry.for_account(account_id) == ()


def test_anonymous_websocket_upgrade_and_scope_close():
    redis = TicketRedis()
    ticket = asyncio.run(
        issue_ticket(
            redis,
            account_id=None,
            access_expires_at=None,
            scopes=(TicketScope.PUBLIC,),
        )
    ).ticket
    app = FastAPI()
    app.state.settings = SimpleNamespace(cors_allowed_origins="http://localhost:5173")
    app.state.redis = redis
    app.state.registry = ConnectionRegistry()
    app.state.session_factory = FakeSessionContext

    @app.websocket("/ws/v1")
    async def endpoint(websocket: WebSocket):
        await handle_connection(websocket)

    with TestClient(app).websocket_connect(
        "/ws/v1",
        subprotocols=("ticketbooking.v1", f"ticketbooking.ticket.{ticket}"),
        headers={"origin": "http://localhost:5173"},
    ) as socket:
        ready = socket.receive_json()
        assert ready["type"] == "connection.ready"
        assert ready["account_id"] is None
        socket.send_json(
            {
                "type": "subscribe",
                "request_id": str(uuid.uuid4()),
                "channels": ["me:participations"],
            }
        )
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 4403


def _listener() -> ClientConnection:
    return ClientConnection(
        websocket=FakeWebSocket(),
        grant=TicketGrant(
            account_id=None,
            access_expires_at=None,
            scopes=(TicketScope.PUBLIC,),
        ),
    )


def _session_changed(session_id: uuid.UUID, reason: str) -> str:
    return json.dumps(
        {
            "type": "session.changed",
            "event_id": str(uuid.uuid4()),
            "session_id": str(session_id),
            "version": 1,
            "reason": reason,
            "occurred_at": "2026-11-23T16:00:00Z",
        }
    )


async def test_new_publication_reaches_connections_that_do_not_know_the_session():
    registry = ConnectionRegistry()
    browsing = _listener()
    watching = _listener()
    session_id = uuid.uuid4()
    await registry.add(browsing)
    await registry.add(watching)
    watching.subscriptions.channels.add(f"session:{session_id}")
    await registry.subscriptions_changed(watching, set())

    payload = _session_changed(session_id, "session_published")
    await deliver(registry, FakeSessionContext, session_channel(session_id), payload)

    assert browsing.websocket.sent == [payload]
    assert watching.websocket.sent == [payload]


async def test_seat_change_stays_on_the_subscribed_session_channel():
    registry = ConnectionRegistry()
    browsing = _listener()
    await registry.add(browsing)
    session_id = uuid.uuid4()

    await deliver(
        registry,
        FakeSessionContext,
        session_channel(session_id),
        _session_changed(session_id, "participation_changed"),
    )

    assert browsing.websocket.sent == []
