"""The single Redis pattern subscription and in-memory routing from a Redis
channel to subscribed connections, including the pre-delivery checks and
``system.resync_required`` after a Redis reconnect.

Design: TECHNICAL_DESIGN.md Sections 10.2 and 10.5.
"""

import asyncio
import contextlib
import json
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.metrics import REDIS_OPERATION_FAILURES, WEBSOCKET_RECONNECTS
from app.core.redis_keys import logical_channel, pubsub_pattern
from app.realtime.gateway.connection import (
    ClientConnection,
    ConnectionRegistry,
    account_is_enabled,
)
from app.schemas.realtime import SessionChangedReason, SystemResyncRequired

# These reasons change which events appear in the public list. The new session
# has no subscribers yet, so the list cannot hear them on its own channel.
_CATALOG_REASONS = frozenset(
    {
        SessionChangedReason.SESSION_PUBLISHED,
        SessionChangedReason.SESSION_CANCELLED,
        SessionChangedReason.EVENT_CANCELLED,
        SessionChangedReason.REGISTRATION_CLOSED,
    }
)


async def run_fanout(
    redis: Redis,
    registry: ConnectionRegistry,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Maintain one pattern subscription for this gateway replica."""
    connected_once = False
    while True:
        try:
            async with redis.pubsub() as pubsub:
                await pubsub.psubscribe(pubsub_pattern())
                if connected_once:
                    WEBSOCKET_RECONNECTS.inc()
                    await _broadcast_resync(registry)
                connected_once = True

                while True:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1
                    )
                    if message is not None and message["type"] == "pmessage":
                        await deliver(
                            registry,
                            session_factory,
                            message["channel"],
                            message["data"],
                        )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - reconnect after dependency failure
            REDIS_OPERATION_FAILURES.labels("pubsub_receive").inc()
            await asyncio.sleep(1)


async def deliver(
    registry: ConnectionRegistry,
    session_factory: async_sessionmaker[AsyncSession],
    redis_channel: str,
    payload: str,
) -> None:
    route = logical_channel(redis_channel)
    if route is None:
        return
    kind, resource_id = route
    message_type = _message_type(payload)

    if kind == "session":
        connections = await registry.for_channel(f"session:{resource_id}")
        if _reason(payload) in _CATALOG_REASONS:
            connections = _with_every_connection(connections, await registry.all())
    elif kind == "management":
        connections = await registry.for_channel(f"event:{resource_id}:management")
    elif message_type == "auth.revoked":
        connections = await registry.for_account(resource_id)
    else:
        connections = tuple(
            connection
            for connection in await registry.for_account(resource_id)
            if "me:participations" in connection.subscriptions.channels
        )

    for connection in connections:
        if (
            kind != "session"
            and message_type != "auth.revoked"
            and not await _private_delivery_allowed(connection, session_factory)
        ):
            continue
        try:
            await connection.send_text(payload, message_type)
            if message_type == "auth.revoked":
                await connection.websocket.close(code=4403)
        except Exception:  # noqa: BLE001 - one dead socket must not block fan-out
            with contextlib.suppress(Exception):
                await connection.websocket.close(code=1011)


async def _private_delivery_allowed(
    connection: ClientConnection,
    session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    account_id = connection.grant.account_id
    if account_id is None:
        return False
    if (
        connection.grant.access_expires_at is not None
        and connection.grant.access_expires_at <= datetime.now(UTC)
    ):
        await connection.websocket.close(code=4401)
        return False
    async with session_factory() as db:
        enabled = await account_is_enabled(db, account_id)
    if not enabled:
        await connection.websocket.close(code=4403)
    return enabled


async def _broadcast_resync(registry: ConnectionRegistry) -> None:
    payload = SystemResyncRequired(occurred_at=datetime.now(UTC)).model_dump_json()
    for connection in await registry.all():
        with contextlib.suppress(Exception):
            await connection.send_text(payload, "system.resync_required")


def _message_type(payload: str) -> str:
    return _string_field(payload, "type") or "unknown"


def _reason(payload: str) -> str | None:
    return _string_field(payload, "reason")


def _string_field(payload: str, name: str) -> str | None:
    try:
        value = json.loads(payload).get(name)
    except json.JSONDecodeError, AttributeError:
        return None
    return value if isinstance(value, str) else None


def _with_every_connection(
    subscribed: tuple[ClientConnection, ...], everyone: tuple[ClientConnection, ...]
) -> tuple[ClientConnection, ...]:
    seen = {connection.connection_id for connection in subscribed}
    return subscribed + tuple(
        connection for connection in everyone if connection.connection_id not in seen
    )
