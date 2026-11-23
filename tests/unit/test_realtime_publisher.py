"""Post-commit publication mapping, ordering, and best-effort behavior."""

import json
import uuid
from datetime import UTC, datetime

from app.realtime.publisher import (
    MANAGEMENT_REASON_BY_CAUSE,
    SESSION_REASON_BY_CAUSE,
    publish_auth_revoked,
    publish_changes,
    publish_event_updated,
)
from app.schemas.realtime import (
    ChangeCause,
    ParticipationChangedReason,
    ParticipationTransition,
    SessionChange,
)


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.queued: list[tuple[str, str]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def publish(self, channel: str, payload: str):
        self.queued.append((channel, payload))
        return self

    async def execute(self, *, raise_on_error: bool):
        assert raise_on_error is False
        self.redis.calls.extend(self.queued)
        return [1] * len(self.queued)


class FakeRedis:
    def __init__(self, fail_first: bool = False):
        self.calls: list[tuple[str, str]] = []
        self.fail_first = fail_first

    async def publish(self, channel: str, payload: str):
        if self.fail_first:
            self.fail_first = False
            raise ConnectionError("simulated")
        self.calls.append((channel, payload))
        return 1

    def pipeline(self, *, transaction: bool):
        assert transaction is False
        return FakePipeline(self)


def _change(
    *,
    event_id: uuid.UUID,
    session_id: uuid.UUID,
    account_id: uuid.UUID,
) -> SessionChange:
    return SessionChange(
        event_id=event_id,
        session_id=session_id,
        version=13,
        cause=ChangeCause.EXPIRATION,
        transitions=(
            ParticipationTransition(
                participation_id=uuid.uuid4(),
                account_id=account_id,
                reason=ParticipationChangedReason.OFFER_CREATED,
            ),
        ),
    )


async def test_publish_changes_uses_contract_order_channels_and_reasons():
    redis = FakeRedis()
    event_id = uuid.uuid4()
    session_id = uuid.uuid4()
    account_id = uuid.uuid4()
    occurred_at = datetime(2026, 11, 14, 22, 0, 1, tzinfo=UTC)

    await publish_changes(
        redis,
        [_change(event_id=event_id, session_id=session_id, account_id=account_id)],
        occurred_at=occurred_at,
    )

    assert [channel for channel, _ in redis.calls] == [
        f"ticketbooking:v1:ps:session:{session_id}",
        f"ticketbooking:v1:ps:account:{account_id}",
        f"ticketbooking:v1:ps:event:{event_id}:management",
    ]
    messages = [json.loads(payload) for _, payload in redis.calls]
    assert [message["type"] for message in messages] == [
        "session.changed",
        "participation.changed",
        "management.changed",
    ]
    assert messages[0]["reason"] == "participation_changed"
    assert messages[1]["reason"] == "offer_created"
    assert messages[2]["reason"] == "roster_changed"
    assert messages[2]["sessions"] == [
        {"session_id": str(session_id), "version": 13}
    ]


async def test_one_public_failure_does_not_stop_private_or_management():
    redis = FakeRedis(fail_first=True)
    change = _change(
        event_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
    )
    await publish_changes(redis, [change])
    assert [json.loads(payload)["type"] for _, payload in redis.calls] == [
        "participation.changed",
        "management.changed",
    ]


async def test_event_update_and_auth_revoke_have_dedicated_messages():
    redis = FakeRedis()
    event_id = uuid.uuid4()
    account_id = uuid.uuid4()
    await publish_event_updated(redis, event_id)
    await publish_auth_revoked(redis, account_id)
    assert [json.loads(payload)["type"] for _, payload in redis.calls] == [
        "management.changed",
        "auth.revoked",
    ]
    assert json.loads(redis.calls[0][1])["sessions"] == []


def test_every_change_cause_has_both_reason_mappings():
    assert set(SESSION_REASON_BY_CAUSE) == set(ChangeCause)
    assert set(MANAGEMENT_REASON_BY_CAUSE) == set(ChangeCause)
