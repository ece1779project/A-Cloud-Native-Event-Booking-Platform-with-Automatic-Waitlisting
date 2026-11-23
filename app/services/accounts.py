"""Account registration, current-account loading, and administrator role and
disablement changes with their audit action and refresh-session revocation.

Design: TECHNICAL_DESIGN.md Sections 8.2 and 9.3.
"""

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError, ErrorCode
from app.core.security import hash_password
from app.db.engine import UNIQUE_VIOLATION, sqlstate
from app.db.locks import RowMissing, lock_account_scope
from app.domain.enums import ManagementActionType, RevokeReason, Role
from app.models.account import Account
from app.models.auth_session import AuthSession
from app.schemas.accounts import AccountCreate
from app.services.audit import record_action


async def register(
    factory: async_sessionmaker[AsyncSession], body: AccountCreate
) -> Account:
    password_hash = hash_password(body.password)
    account = Account(
        email=body.email,
        display_name=body.display_name,
        password_hash=password_hash,
        role=Role.ATTENDEE.value,
    )
    try:
        async with factory.begin() as db:
            db.add(account)
            await db.flush()
            await db.refresh(account)
    except IntegrityError as exc:
        if sqlstate(exc) == UNIQUE_VIOLATION:
            raise AppError(
                ErrorCode.EMAIL_ALREADY_REGISTERED, details={"field": "email"}
            ) from exc
        raise
    return account


async def load_account(db: AsyncSession, account_id: uuid.UUID) -> Account | None:
    return await db.get(Account, account_id)


async def find_by_email(db: AsyncSession, email: str) -> Account | None:
    return await db.scalar(
        select(Account).where(func.lower(Account.email) == email.strip().lower())
    )


async def list_accounts(
    factory: async_sessionmaker[AsyncSession], *, after_id: uuid.UUID | None, limit: int
) -> list[Account]:
    stmt = select(Account).order_by(Account.id).limit(limit + 1)
    if after_id is not None:
        stmt = stmt.where(Account.id > after_id)
    async with factory() as db:
        return list(await db.scalars(stmt))


async def admin_patch_account(
    factory: async_sessionmaker[AsyncSession],
    *,
    actor: Account,
    target_id: uuid.UUID,
    role: Role | None,
    disabled: bool | None,
    reason: str,
) -> Account:
    if target_id == actor.id:
        raise AppError(
            ErrorCode.FORBIDDEN, "Administrators cannot change their own account."
        )
    correlation_id = uuid.uuid7()

    async with factory.begin() as db:
        try:
            locked = await lock_account_scope(db, exclusive_ids=[target_id])
        except RowMissing as exc:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND) from exc
        target, now = locked.accounts[target_id], locked.now
        if target.role == Role.ADMIN.value:
            raise AppError(
                ErrorCode.FORBIDDEN, "Administrator accounts cannot be changed."
            )

        async def audit(action: ManagementActionType) -> None:
            await record_action(
                db,
                actor_id=actor.id,
                action=action,
                reason=reason,
                occurred_at=now,
                correlation_id=correlation_id,
                target_account_id=target.id,
            )

        if role is not None and role.value != target.role:
            target.role = role.value
            await audit(ManagementActionType.ROLE_CHANGED)

        if disabled is True and target.disabled_at is None:
            target.disabled_at = now
            await db.execute(
                update(AuthSession)
                .where(
                    AuthSession.account_id == target.id,
                    AuthSession.revoked_at.is_(None),
                )
                .values(
                    revoked_at=now, revoke_reason=RevokeReason.ACCOUNT_DISABLED.value
                )
            )
            await audit(ManagementActionType.ACCOUNT_DISABLED)
        elif disabled is False and target.disabled_at is not None:
            target.disabled_at = None
            await audit(ManagementActionType.ACCOUNT_ENABLED)
    # TODO(realtime): publish the post-commit control invalidation (Section 9.3 step 4).
    return target
