"""One client connection: the upgrade and ticket check, ``connection.ready``,
heartbeat, periodic revalidation of the account, and close codes.

Design: TECHNICAL_DESIGN.md Sections 9.4 and 10.2.
"""

import asyncio
import contextlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import WebSocket
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.websockets import WebSocketDisconnect

from app.core.constants import (
    WS_AUTH_REVALIDATION_SECONDS,
    WS_HEARTBEAT_SECONDS,
)
from app.core.metrics import WEBSOCKET_CONNECTIONS, WEBSOCKET_MESSAGES
from app.models.account import Account
from app.realtime.gateway.subscriptions import (
    ScopeViolation,
    SubscriptionLimitExceeded,
    SubscriptionState,
    apply_request,
)
from app.realtime.tickets import TicketError, TicketGrant, consume_ticket
from app.schemas.realtime import (
    ConnectionReady,
    SubscriptionRequest,
)

APPLICATION_PROTOCOL = "seatflow.v1"
TICKET_PROTOCOL_PREFIX = "seatflow.ticket."


@dataclass
class ClientConnection:
    websocket: WebSocket
    grant: TicketGrant
    connection_id: uuid.UUID = field(default_factory=uuid.uuid4)
    subscriptions: SubscriptionState = field(init=False)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self.subscriptions = SubscriptionState(scopes=frozenset(self.grant.scopes))

    async def send_text(self, payload: str, message_type: str) -> None:
        async with self.send_lock:
            await self.websocket.send_text(payload)
        WEBSOCKET_MESSAGES.labels("outbound", message_type).inc()


class ConnectionRegistry:
    """Connection and logical-channel indexes local to one gateway replica."""

    def __init__(self) -> None:
        self.connections: dict[uuid.UUID, ClientConnection] = {}
        self.channels: dict[str, set[uuid.UUID]] = {}
        self.accounts: dict[uuid.UUID, set[uuid.UUID]] = {}
        self._lock = asyncio.Lock()

    async def add(self, connection: ClientConnection) -> None:
        async with self._lock:
            self.connections[connection.connection_id] = connection
            if connection.grant.account_id is not None:
                self.accounts.setdefault(connection.grant.account_id, set()).add(
                    connection.connection_id
                )

    async def remove(self, connection: ClientConnection) -> None:
        async with self._lock:
            self.connections.pop(connection.connection_id, None)
            for connection_ids in self.channels.values():
                connection_ids.discard(connection.connection_id)
            self.channels = {
                name: ids for name, ids in self.channels.items() if ids
            }
            if connection.grant.account_id is not None:
                ids = self.accounts.get(connection.grant.account_id)
                if ids is not None:
                    ids.discard(connection.connection_id)
                    if not ids:
                        self.accounts.pop(connection.grant.account_id, None)

    async def subscriptions_changed(
        self, connection: ClientConnection, previous: set[str]
    ) -> None:
        async with self._lock:
            removed = previous - connection.subscriptions.channels
            added = connection.subscriptions.channels - previous
            for name in removed:
                ids = self.channels.get(name)
                if ids is not None:
                    ids.discard(connection.connection_id)
                    if not ids:
                        self.channels.pop(name, None)
            for name in added:
                self.channels.setdefault(name, set()).add(connection.connection_id)

    async def for_channel(self, channel: str) -> tuple[ClientConnection, ...]:
        async with self._lock:
            return tuple(
                self.connections[connection_id]
                for connection_id in self.channels.get(channel, ())
                if connection_id in self.connections
            )

    async def for_account(
        self, account_id: uuid.UUID
    ) -> tuple[ClientConnection, ...]:
        async with self._lock:
            return tuple(
                self.connections[connection_id]
                for connection_id in self.accounts.get(account_id, ())
                if connection_id in self.connections
            )

    async def all(self) -> tuple[ClientConnection, ...]:
        async with self._lock:
            return tuple(self.connections.values())


async def handle_connection(websocket: WebSocket) -> None:
    app = websocket.app
    protocols = _offered_protocols(websocket)
    ticket = _extract_ticket(protocols)
    allowed_origins = {
        origin.strip()
        for origin in app.state.settings.cors_allowed_origins.split(",")
        if origin.strip()
    }
    if (
        websocket.headers.get("origin") not in allowed_origins
        or APPLICATION_PROTOCOL not in protocols
        or ticket is None
    ):
        await _close_upgrade(websocket, 4403)
        return

    try:
        grant = await consume_ticket(app.state.redis, ticket)
    except TicketError:
        await _close_upgrade(websocket, 4401)
        return
    except Exception:  # noqa: BLE001 - Redis outage rejects the upgrade
        await _close_upgrade(websocket, 1011)
        return

    await websocket.accept(subprotocol=APPLICATION_PROTOCOL)
    connection = ClientConnection(websocket=websocket, grant=grant)
    registry: ConnectionRegistry = app.state.registry
    await registry.add(connection)
    WEBSOCKET_CONNECTIONS.inc()
    revalidation = asyncio.create_task(
        _revalidate_loop(connection, app.state.session_factory)
    )
    try:
        await connection.send_text(
            ConnectionReady(
                connection_id=connection.connection_id,
                account_id=grant.account_id,
                heartbeat_interval_seconds=WS_HEARTBEAT_SECONDS,
            ).model_dump_json(),
            "connection.ready",
        )
        await _receive_loop(connection, registry, app.state.session_factory)
    except WebSocketDisconnect:
        pass
    finally:
        revalidation.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await revalidation
        await registry.remove(connection)
        WEBSOCKET_CONNECTIONS.dec()


async def _receive_loop(
    connection: ClientConnection,
    registry: ConnectionRegistry,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    while True:
        raw = await connection.websocket.receive_text()
        try:
            request = SubscriptionRequest.model_validate_json(raw)
        except ValidationError:
            await connection.websocket.close(code=4408)
            return
        WEBSOCKET_MESSAGES.labels("inbound", request.type).inc()

        previous = set(connection.subscriptions.channels)
        try:
            async with session_factory() as db:
                ack = await apply_request(
                    db,
                    connection.subscriptions,
                    request,
                    account_id=connection.grant.account_id,
                )
        except ScopeViolation:
            await connection.websocket.close(code=4403)
            return
        except SubscriptionLimitExceeded:
            await connection.websocket.close(code=4408)
            return

        await registry.subscriptions_changed(connection, previous)
        await connection.send_text(ack.model_dump_json(), "subscription.ack")


async def _revalidate_loop(
    connection: ClientConnection,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if connection.grant.account_id is None:
        return
    while True:
        delay = WS_AUTH_REVALIDATION_SECONDS
        if connection.grant.access_expires_at is not None:
            delay = min(
                delay,
                max(
                    0,
                    (
                        connection.grant.access_expires_at - datetime.now(UTC)
                    ).total_seconds(),
                ),
            )
        await asyncio.sleep(delay)
        if (
            connection.grant.access_expires_at is not None
            and connection.grant.access_expires_at <= datetime.now(UTC)
        ):
            await connection.websocket.close(code=4401)
            return
        async with session_factory() as db:
            if not await account_is_enabled(db, connection.grant.account_id):
                await connection.websocket.close(code=4403)
                return


async def account_is_enabled(db: AsyncSession, account_id: uuid.UUID) -> bool:
    return (
        await db.scalar(
            select(Account.id).where(
                Account.id == account_id, Account.disabled_at.is_(None)
            )
        )
        is not None
    )


def _offered_protocols(websocket: WebSocket) -> tuple[str, ...]:
    return tuple(
        protocol.strip()
        for protocol in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if protocol.strip()
    )


def _extract_ticket(protocols: tuple[str, ...]) -> str | None:
    tickets = [
        protocol.removeprefix(TICKET_PROTOCOL_PREFIX)
        for protocol in protocols
        if protocol.startswith(TICKET_PROTOCOL_PREFIX)
    ]
    return tickets[0] if len(tickets) == 1 and tickets[0] else None


async def _close_upgrade(websocket: WebSocket, code: int) -> None:
    await websocket.accept()
    await websocket.close(code=code)
