"""Post-commit, best-effort publication of invalidation messages to Redis
Pub/Sub. Callers pass the ``SessionChange`` values of one commit; ``None``
is not published. A failure is counted and logged and never fails the
committed command.

Design: TECHNICAL_DESIGN.md Sections 10.3 and 10.5.
"""

import asyncio
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from redis.asyncio import Redis

from app.core.metrics import REALTIME_PUBLISH_FAILURES, REDIS_OPERATION_FAILURES
from app.core.redis_keys import account_channel, management_channel, session_channel
from app.schemas.realtime import (
    AuthRevoked,
    ChangeCause,
    ManagementChanged,
    ManagementChangedReason,
    ParticipationChanged,
    SessionChange,
    SessionChanged,
    SessionChangedReason,
    SessionVersion,
)

logger = logging.getLogger(__name__)
_PUBLISH_TIMEOUT_SECONDS = 1

SESSION_REASON_BY_CAUSE: dict[ChangeCause, SessionChangedReason] = {
    ChangeCause.BOOKING_HOLD: SessionChangedReason.PARTICIPATION_CHANGED,
    ChangeCause.WAITLIST_JOIN: SessionChangedReason.PARTICIPATION_CHANGED,
    ChangeCause.CONFIRMATION: SessionChangedReason.PARTICIPATION_CHANGED,
    ChangeCause.ATTENDEE_RELEASE: SessionChangedReason.PARTICIPATION_CHANGED,
    ChangeCause.ADMIN_PARTICIPATION_CANCEL: (
        SessionChangedReason.PARTICIPATION_CHANGED
    ),
    ChangeCause.EXPIRATION: SessionChangedReason.PARTICIPATION_CHANGED,
    ChangeCause.PROMOTION: SessionChangedReason.PARTICIPATION_CHANGED,
    ChangeCause.REGISTRATION_CLOSED: SessionChangedReason.REGISTRATION_CLOSED,
    ChangeCause.CAPACITY_CHANGED: SessionChangedReason.CAPACITY_CHANGED,
    ChangeCause.SESSION_PUBLISHED: SessionChangedReason.SESSION_PUBLISHED,
    ChangeCause.EVENT_PUBLISHED: SessionChangedReason.SESSION_PUBLISHED,
    ChangeCause.SESSION_CANCELLED: SessionChangedReason.SESSION_CANCELLED,
    ChangeCause.EVENT_CANCELLED: SessionChangedReason.EVENT_CANCELLED,
}

MANAGEMENT_REASON_BY_CAUSE: dict[ChangeCause, ManagementChangedReason] = {
    ChangeCause.BOOKING_HOLD: ManagementChangedReason.ROSTER_CHANGED,
    ChangeCause.WAITLIST_JOIN: ManagementChangedReason.ROSTER_CHANGED,
    ChangeCause.CONFIRMATION: ManagementChangedReason.ROSTER_CHANGED,
    ChangeCause.ATTENDEE_RELEASE: ManagementChangedReason.ROSTER_CHANGED,
    ChangeCause.ADMIN_PARTICIPATION_CANCEL: (
        ManagementChangedReason.ROSTER_CHANGED
    ),
    ChangeCause.EXPIRATION: ManagementChangedReason.ROSTER_CHANGED,
    ChangeCause.PROMOTION: ManagementChangedReason.ROSTER_CHANGED,
    ChangeCause.REGISTRATION_CLOSED: ManagementChangedReason.ROSTER_CHANGED,
    ChangeCause.CAPACITY_CHANGED: ManagementChangedReason.SESSION_UPDATED,
    ChangeCause.SESSION_PUBLISHED: ManagementChangedReason.SESSION_UPDATED,
    ChangeCause.EVENT_PUBLISHED: ManagementChangedReason.EVENT_UPDATED,
    ChangeCause.SESSION_CANCELLED: ManagementChangedReason.SESSION_UPDATED,
    ChangeCause.EVENT_CANCELLED: ManagementChangedReason.EVENT_UPDATED,
}


async def publish_changes(
    redis: Redis,
    changes: Sequence[SessionChange],
    *,
    occurred_at: datetime | None = None,
    correlation_id: str | None = None,
) -> None:
    """Publish one committed transaction in public, private, management order."""
    if not changes:
        return
    event_id = changes[0].event_id
    cause = changes[0].cause
    if any(change.event_id != event_id or change.cause != cause for change in changes):
        raise ValueError("one publication call must describe one event and cause")
    timestamp = occurred_at or datetime.now(UTC)

    for change in changes:
        message = SessionChanged(
            event_id=change.event_id,
            session_id=change.session_id,
            version=change.version,
            reason=SESSION_REASON_BY_CAUSE[change.cause],
            occurred_at=timestamp,
        )
        await _publish_one(
            redis,
            session_channel(change.session_id),
            message.model_dump_json(),
            message.type,
            correlation_id,
        )

    await _publish_private_messages(
        redis, changes, timestamp, correlation_id=correlation_id
    )

    management = ManagementChanged(
        event_id=event_id,
        sessions=tuple(
            SessionVersion(session_id=change.session_id, version=change.version)
            for change in changes
        ),
        reason=MANAGEMENT_REASON_BY_CAUSE[cause],
        occurred_at=timestamp,
    )
    await _publish_one(
        redis,
        management_channel(event_id),
        management.model_dump_json(),
        management.type,
        correlation_id,
    )


async def publish_event_updated(
    redis: Redis,
    event_id: uuid.UUID,
    *,
    occurred_at: datetime | None = None,
    correlation_id: str | None = None,
) -> None:
    message = ManagementChanged(
        event_id=event_id,
        sessions=(),
        reason=ManagementChangedReason.EVENT_UPDATED,
        occurred_at=occurred_at or datetime.now(UTC),
    )
    await _publish_one(
        redis,
        management_channel(event_id),
        message.model_dump_json(),
        message.type,
        correlation_id,
    )


async def publish_auth_revoked(
    redis: Redis,
    account_id: uuid.UUID,
    *,
    occurred_at: datetime | None = None,
    correlation_id: str | None = None,
) -> None:
    message = AuthRevoked(occurred_at=occurred_at or datetime.now(UTC))
    await _publish_one(
        redis,
        account_channel(account_id),
        message.model_dump_json(),
        message.type,
        correlation_id,
    )


async def _publish_private_messages(
    redis: Redis,
    changes: Sequence[SessionChange],
    occurred_at: datetime,
    *,
    correlation_id: str | None,
) -> None:
    publications = [
        (
            account_channel(transition.account_id),
            ParticipationChanged(
                session_id=change.session_id,
                participation_id=transition.participation_id,
                version=change.version,
                reason=transition.reason,
                occurred_at=occurred_at,
            ),
        )
        for change in changes
        for transition in change.transitions
    ]
    if not publications:
        return

    try:
        async with redis.pipeline(transaction=False) as pipeline:
            for channel, message in publications:
                pipeline.publish(channel, message.model_dump_json())
            results = await asyncio.wait_for(
                pipeline.execute(raise_on_error=False),
                timeout=_PUBLISH_TIMEOUT_SECONDS,
            )
    except Exception as exc:  # noqa: BLE001 - committed work must still succeed
        for _channel, message in publications:
            _record_failure(message.type, correlation_id, exc)
        return

    for result, (_channel, message) in zip(results, publications, strict=True):
        if isinstance(result, Exception):
            _record_failure(message.type, correlation_id, result)


async def _publish_one(
    redis: Redis,
    channel: str,
    payload: str,
    message_type: str,
    correlation_id: str | None,
) -> None:
    try:
        await asyncio.wait_for(
            redis.publish(channel, payload), timeout=_PUBLISH_TIMEOUT_SECONDS
        )
    except Exception as exc:  # noqa: BLE001 - committed work must still succeed
        _record_failure(message_type, correlation_id, exc)


def _record_failure(
    message_type: str, correlation_id: str | None, exc: BaseException
) -> None:
    REALTIME_PUBLISH_FAILURES.labels(message_type).inc()
    REDIS_OPERATION_FAILURES.labels("publish").inc()
    logger.warning(
        "realtime publication failed",
        extra={
            "operation": "realtime_publish",
            "message_type": message_type,
            "correlation_id": correlation_id,
            "error_type": type(exc).__name__,
        },
    )
