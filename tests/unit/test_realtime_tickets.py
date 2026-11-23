"""Single-use WebSocket ticket behavior without a Redis server."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from redis.exceptions import RedisError

from app.core.metrics import REDIS_OPERATION_FAILURES
from app.realtime.tickets import TicketError, consume_ticket, issue_ticket
from app.schemas.realtime import TicketScope


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.last_expiry: int | None = None

    async def set(self, name: str, value: str, *, ex: int, nx: bool):
        assert nx is True
        self.last_expiry = ex
        if name in self.values:
            return None
        self.values[name] = value
        return True

    async def getdel(self, name: str):
        return self.values.pop(name, None)


class FailingRedis(FakeRedis):
    async def set(self, name: str, value: str, *, ex: int, nx: bool):
        raise RedisError("down")

    async def getdel(self, name: str):
        raise RedisError("down")


async def test_ticket_round_trip_is_single_use_and_stores_no_secret():
    redis = FakeRedis()
    account_id = uuid.uuid4()
    expires_at = datetime.now(UTC) + timedelta(minutes=5)

    response = await issue_ticket(
        redis,
        account_id=account_id,
        access_expires_at=expires_at,
        scopes=(TicketScope.PUBLIC, TicketScope.ACCOUNT),
    )

    assert response.expires_in == 30
    assert response.ticket not in next(iter(redis.values))
    assert redis.last_expiry == 30

    grant = await consume_ticket(redis, response.ticket)
    assert grant.account_id == account_id
    assert grant.scopes == (TicketScope.PUBLIC, TicketScope.ACCOUNT)

    with pytest.raises(TicketError, match="already used"):
        await consume_ticket(redis, response.ticket)


async def test_expired_authentication_is_rejected_after_atomic_consumption():
    redis = FakeRedis()
    now = datetime.now(UTC)
    response = await issue_ticket(
        redis,
        account_id=uuid.uuid4(),
        access_expires_at=now - timedelta(seconds=1),
        scopes=(TicketScope.PUBLIC, TicketScope.ACCOUNT),
    )

    with pytest.raises(TicketError, match="authentication expired"):
        await consume_ticket(redis, response.ticket, now=now)

    with pytest.raises(TicketError, match="already used"):
        await consume_ticket(redis, response.ticket, now=now)


async def test_anonymous_ticket_has_only_public_scope():
    redis = FakeRedis()
    response = await issue_ticket(
        redis,
        account_id=None,
        access_expires_at=None,
        scopes=(TicketScope.PUBLIC,),
    )
    grant = await consume_ticket(redis, response.ticket)
    assert grant.account_id is None
    assert grant.scopes == (TicketScope.PUBLIC,)


@pytest.mark.parametrize(
    ("account_id", "expires_at", "scopes"),
    [
        (None, None, (TicketScope.PUBLIC, TicketScope.ACCOUNT)),
        (uuid.uuid4(), None, (TicketScope.PUBLIC, TicketScope.ACCOUNT)),
        (
            uuid.uuid4(),
            datetime.now(UTC) + timedelta(minutes=5),
            (TicketScope.PUBLIC,),
        ),
    ],
)
async def test_invalid_identity_scope_combinations_are_not_issued(
    account_id, expires_at, scopes
):
    with pytest.raises(ValueError):
        await issue_ticket(
            FakeRedis(),
            account_id=account_id,
            access_expires_at=expires_at,
            scopes=scopes,
        )


async def test_redis_ticket_failures_are_counted():
    issue_metric = REDIS_OPERATION_FAILURES.labels("ticket_issue")
    consume_metric = REDIS_OPERATION_FAILURES.labels("ticket_consume")
    issue_before = issue_metric._value.get()
    consume_before = consume_metric._value.get()

    with pytest.raises(RedisError):
        await issue_ticket(
            FailingRedis(),
            account_id=None,
            access_expires_at=None,
            scopes=(TicketScope.PUBLIC,),
        )
    with pytest.raises(RedisError):
        await consume_ticket(FailingRedis(), "ticket")

    assert issue_metric._value.get() == issue_before + 1
    assert consume_metric._value.get() == consume_before + 1
