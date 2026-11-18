import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.core.errors import AppError, ErrorCode
from app.models.request_result import RequestResult
try:
    from app.services.idempotency import CommandOutcome, parameter_hash, run_command
except ImportError as exc:
    pytest.skip(f"第 07 章未完成: {exc}", allow_module_level=True)
from tests.factories import make_account


def test_parameter_hash_is_canonical():
    sid = uuid.uuid4()
    assert parameter_hash("BOOK", session_id=sid, x=1) == parameter_hash("BOOK", x=1, session_id=str(sid))
    assert parameter_hash("BOOK", session_id=sid) != parameter_hash("JOIN", session_id=sid)


@pytest.fixture
async def account(session_factory):
    async with session_factory.begin() as db:
        return await make_account(db)


def _ok(counter):
    async def execute(db, ctx):
        if (r := await ctx.recheck(db)) is not None:
            return r
        counter.append(1)
        return CommandOutcome(http_status=201, result_code="HELD", payload={"n": len(counter)})
    return execute


async def test_replay_returns_original(session_factory, account):
    calls, key = [], uuid.uuid4()
    ph = parameter_hash("BOOK", session_id="s1")
    a = await run_command(session_factory, account_id=account.id, request_id=key,
                          operation="BOOK", phash=ph, execute=_ok(calls))
    b = await run_command(session_factory, account_id=account.id, request_id=key,
                          operation="BOOK", phash=ph, execute=_ok(calls))
    assert len(calls) == 1
    assert (b.http_status, b.payload, b.replayed) == (201, a.payload, True)


async def test_same_key_different_params_conflicts(session_factory, account):
    key = uuid.uuid4()
    await run_command(session_factory, account_id=account.id, request_id=key, operation="BOOK",
                      phash=parameter_hash("BOOK", session_id="s1"), execute=_ok([]))
    with pytest.raises(AppError) as info:
        await run_command(session_factory, account_id=account.id, request_id=key, operation="BOOK",
                          phash=parameter_hash("BOOK", session_id="s2"), execute=_ok([]))
    assert info.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


async def test_failure_before_receipt_leaves_key_reusable(session_factory, account):
    key, ph = uuid.uuid4(), parameter_hash("BOOK", session_id="s1")

    async def boom(db, ctx):
        raise AppError(ErrorCode.LOCK_TIMEOUT)

    with pytest.raises(AppError):
        await run_command(session_factory, account_id=account.id, request_id=key,
                          operation="BOOK", phash=ph, execute=boom)
    out = await run_command(session_factory, account_id=account.id, request_id=key,
                            operation="BOOK", phash=ph, execute=_ok([]))
    assert out.http_status == 201 and not out.replayed


async def test_concurrent_same_key_runs_once(session_factory, account):
    calls, key, ph = [], uuid.uuid4(), parameter_hash("BOOK", session_id="s1")

    async def slow(db, ctx):
        calls.append(1)
        await asyncio.sleep(0.2)
        return CommandOutcome(http_status=201, result_code="HELD", payload={})

    results = await asyncio.gather(*[
        run_command(session_factory, account_id=account.id, request_id=key,
                    operation="BOOK", phash=ph, execute=slow) for _ in range(3)])
    assert {r.http_status for r in results} == {201}
    assert sum(not r.replayed for r in results) == 1
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(RequestResult)) == 1


async def test_keys_are_scoped_per_account(session_factory, account):
    async with session_factory.begin() as db:
        other = await make_account(db)
    key, ph = uuid.uuid4(), parameter_hash("BOOK", session_id="s1")
    a = await run_command(session_factory, account_id=account.id, request_id=key,
                          operation="BOOK", phash=ph, execute=_ok([]))
    b = await run_command(session_factory, account_id=other.id, request_id=key,
                          operation="BOOK", phash=ph, execute=_ok([]))
    assert not a.replayed and not b.replayed
