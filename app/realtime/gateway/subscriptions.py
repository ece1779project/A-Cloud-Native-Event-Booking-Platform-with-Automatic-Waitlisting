"""``subscribe`` and ``unsubscribe`` handling: channel-name parsing, scope and
ownership checks, per-connection limits and ``subscription.ack``.

Design: TECHNICAL_DESIGN.md Section 10.2.
"""

import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import WS_PUBLIC_SUBSCRIPTION_LIMIT
from app.domain.enums import PublicationStatus, Role
from app.models.account import Account
from app.models.event import Event
from app.models.event_session import EventSession
from app.schemas.realtime import (
    SubscriptionAck,
    SubscriptionRejection,
    SubscriptionRequest,
    TicketScope,
)


class ChannelKind(StrEnum):
    SESSION = "session"
    ACCOUNT = "account"
    MANAGEMENT = "management"


@dataclass(frozen=True)
class Channel:
    name: str
    kind: ChannelKind
    resource_id: uuid.UUID | None = None


@dataclass
class SubscriptionState:
    scopes: frozenset[TicketScope]
    channels: set[str] = field(default_factory=set)


class ScopeViolation(Exception):
    """A well-formed channel is outside the connection ticket's scope."""


class SubscriptionLimitExceeded(Exception):
    """A subscribe command exceeds a connection policy limit."""


def parse_channel(value: str) -> Channel | None:
    if value == "me:participations":
        return Channel(value, ChannelKind.ACCOUNT)

    parts = value.split(":")
    if len(parts) == 2 and parts[0] == "session":
        resource_id = _canonical_uuid(parts[1])
        if resource_id is not None:
            return Channel(value, ChannelKind.SESSION, resource_id)
    if len(parts) == 3 and parts[0] == "event" and parts[2] == "management":
        resource_id = _canonical_uuid(parts[1])
        if resource_id is not None:
            return Channel(value, ChannelKind.MANAGEMENT, resource_id)
    return None


def _canonical_uuid(value: str) -> uuid.UUID | None:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        return None
    return parsed if str(parsed) == value else None


async def apply_request(
    db: AsyncSession,
    state: SubscriptionState,
    request: SubscriptionRequest,
    *,
    account_id: uuid.UUID | None,
) -> SubscriptionAck:
    """Apply one request atomically after validating all policy violations."""
    parsed = [(name, parse_channel(name)) for name in request.channels]
    _check_scopes(state.scopes, (channel for _, channel in parsed))

    if request.type == "unsubscribe":
        accepted = tuple(
            name
            for name, channel in parsed
            if channel is not None and name in state.channels
        )
        state.channels.difference_update(accepted)
        return SubscriptionAck(
            request_id=request.request_id,
            accepted=accepted,
            rejected=tuple(
                SubscriptionRejection(channel=name, code="INVALID_CHANNEL")
                for name, channel in parsed
                if channel is None
            ),
        )

    requested_public = {
        channel.name
        for _, channel in parsed
        if channel is not None and channel.kind is ChannelKind.SESSION
    }
    current_public = {
        name
        for name in state.channels
        if (parsed_channel := parse_channel(name)) is not None
        and parsed_channel.kind is ChannelKind.SESSION
    }
    if len(current_public | requested_public) > WS_PUBLIC_SUBSCRIPTION_LIMIT:
        raise SubscriptionLimitExceeded

    accepted: list[str] = []
    rejected: list[SubscriptionRejection] = []
    for name, channel in parsed:
        if channel is None:
            rejected.append(
                SubscriptionRejection(channel=name, code="INVALID_CHANNEL")
            )
        elif channel.kind is ChannelKind.SESSION:
            if await _published_session_exists(db, channel.resource_id):
                accepted.append(name)
            else:
                rejected.append(
                    SubscriptionRejection(
                        channel=name, code="RESOURCE_NOT_FOUND"
                    )
                )
        elif channel.kind is ChannelKind.MANAGEMENT:
            if account_id is not None and await _can_manage(
                db, account_id, channel.resource_id
            ):
                accepted.append(name)
            else:
                rejected.append(
                    SubscriptionRejection(channel=name, code="FORBIDDEN")
                )
        else:
            accepted.append(name)

    state.channels.update(accepted)
    return SubscriptionAck(
        request_id=request.request_id,
        accepted=tuple(accepted),
        rejected=tuple(rejected),
    )


def _check_scopes(
    scopes: frozenset[TicketScope], channels
) -> None:
    for channel in channels:
        if channel is None:
            continue
        required = {
            ChannelKind.SESSION: TicketScope.PUBLIC,
            ChannelKind.ACCOUNT: TicketScope.ACCOUNT,
            ChannelKind.MANAGEMENT: TicketScope.MANAGEMENT,
        }[channel.kind]
        if required not in scopes:
            raise ScopeViolation


async def _published_session_exists(
    db: AsyncSession, session_id: uuid.UUID | None
) -> bool:
    return (
        await db.scalar(
            select(EventSession.id)
            .join(Event, Event.id == EventSession.event_id)
            .where(
                EventSession.id == session_id,
                EventSession.status == PublicationStatus.PUBLISHED,
                Event.status == PublicationStatus.PUBLISHED,
            )
        )
        is not None
    )


async def _can_manage(
    db: AsyncSession, account_id: uuid.UUID, event_id: uuid.UUID | None
) -> bool:
    row = (
        await db.execute(
            select(Account.role, Event.organizer_id)
            .select_from(Account, Event)
            .where(
                Account.id == account_id,
                Account.disabled_at.is_(None),
                Event.id == event_id,
            )
        )
    ).one_or_none()
    if row is None:
        return False
    role, organizer_id = row
    return role == Role.ADMIN or (
        role == Role.ORGANIZER and organizer_id == account_id
    )
