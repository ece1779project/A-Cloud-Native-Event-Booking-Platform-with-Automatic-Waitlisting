"""Login, refresh-token rotation with reuse detection, and logout.

Design: TECHNICAL_DESIGN.md Sections 6.2 and 9; DATABASE_DESIGN.md Section 2.5.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.constants import REFRESH_ROTATION_GRACE_SECONDS
from app.core.errors import AppError, ErrorCode
from app.core.security import (
    AccessToken,
    digest,
    digest_matches,
    hash_password,
    ip_prefix,
    issue_access_token,
    new_token,
    password_needs_rehash,
    verify_password,
)
from app.db.locks import lock_account_scope, lock_refresh_session
from app.domain.enums import RevokeReason
from app.models.account import Account
from app.models.auth_session import AuthSession


@dataclass(frozen=True, slots=True)
class IssuedLogin:
    account: Account
    access: AccessToken
    refresh_token: str
    csrf_token: str | None
    refresh_expires_at: datetime
    now: datetime


def _issue_access(settings: Settings, account_id: uuid.UUID, now: datetime) -> AccessToken:
    private_key, _ = settings.require_jwt_keys()
    return issue_access_token(
        account_id=account_id,
        now=now,
        private_key=private_key,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        ttl_seconds=settings.access_token_ttl_seconds,
    )


def _csrf_ok(csrf_header: str | None, row: AuthSession) -> bool:
    return bool(csrf_header) and digest_matches(csrf_header, row.csrf_token_hash)


# ---------------- login ----------------


async def login(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    email: str,
    password: str,
    user_agent: str | None,
    client_ip: str | None,
) -> IssuedLogin:
    async with factory() as db:
        account = await db.scalar(select(Account).where(func.lower(Account.email) == email))
    if not verify_password(account.password_hash if account else None, password):
        raise AppError(ErrorCode.INVALID_CREDENTIALS)
    assert account is not None
    new_hash = hash_password(password) if password_needs_rehash(account.password_hash) else None

    refresh, csrf = new_token(), new_token()
    async with factory.begin() as db:
        locked = await lock_account_scope(db, share_ids=[account.id])
        account = locked.accounts[account.id]
        if account.disabled_at is not None:
            raise AppError(ErrorCode.ACCOUNT_DISABLED)
        now = locked.now
        expires_at = now + timedelta(seconds=settings.refresh_token_ttl_seconds)
        db.add(
            AuthSession(
                account_id=account.id,
                family_id=uuid.uuid7(),
                refresh_token_hash=digest(refresh),
                csrf_token_hash=digest(csrf),
                created_at=now,
                last_used_at=now,
                expires_at=expires_at,
                user_agent_hash=digest(user_agent) if user_agent else None,
                last_ip_prefix=ip_prefix(client_ip),
            )
        )
    if new_hash is not None:
        await _rehash(factory, account.id, new_hash)
    return IssuedLogin(
        account, _issue_access(settings, account.id, now), refresh, csrf, expires_at, now
    )


async def _rehash(
    factory: async_sessionmaker[AsyncSession], account_id: uuid.UUID, new_hash: str
) -> None:
    async with factory.begin() as db:
        await db.execute(
            update(Account).where(Account.id == account_id).values(password_hash=new_hash)
        )


# ---------------- refresh ----------------


async def _revoke_family(
    db: AsyncSession, family_id: uuid.UUID, now: datetime, reason: RevokeReason
) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.family_id == family_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now, revoke_reason=reason.value)
    )


async def refresh(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    refresh_token: str | None,
    csrf_header: str | None,
) -> IssuedLogin:
    if not refresh_token:
        raise AppError(ErrorCode.AUTHENTICATION_REQUIRED)

    error: AppError | None = None
    new_refresh = new_token()
    async with factory.begin() as db:
        locked = await lock_refresh_session(db, digest(refresh_token))
        if locked is None:
            raise AppError(ErrorCode.AUTHENTICATION_REQUIRED)
        account, row, now = locked.account, locked.session, locked.now

        if not _csrf_ok(csrf_header, row):
            raise AppError(ErrorCode.FORBIDDEN, "CSRF token mismatch.")
        if row.revoked_at is not None or row.expires_at <= now:
            raise AppError(ErrorCode.AUTHENTICATION_REQUIRED)
        if account.disabled_at is not None:
            raise AppError(ErrorCode.ACCOUNT_DISABLED)

        if row.rotated_at is not None:
            if now - row.rotated_at < timedelta(seconds=REFRESH_ROTATION_GRACE_SECONDS):
                raise AppError(ErrorCode.REFRESH_ALREADY_ROTATED)
            await _revoke_family(db, row.family_id, now, RevokeReason.REUSE_DETECTED)
            error = AppError(ErrorCode.AUTHENTICATION_REQUIRED, "Refresh token reuse detected.")
        else:
            row.rotated_at = now
            row.last_used_at = now
            db.add(
                AuthSession(
                    account_id=row.account_id,
                    family_id=row.family_id,
                    refresh_token_hash=digest(new_refresh),
                    csrf_token_hash=row.csrf_token_hash,
                    created_at=now,
                    last_used_at=now,
                    expires_at=row.expires_at,
                    user_agent_hash=row.user_agent_hash,
                    last_ip_prefix=row.last_ip_prefix,
                )
            )
    if error is not None:
        raise error
    return IssuedLogin(
        account,
        _issue_access(settings, account.id, now),
        new_refresh,
        None,
        row.expires_at,
        now,
    )


# ---------------- logout ----------------


async def logout(
    factory: async_sessionmaker[AsyncSession],
    *,
    refresh_token: str | None,
    csrf_header: str | None,
) -> None:
    if not refresh_token:
        return
    async with factory.begin() as db:
        locked = await lock_refresh_session(db, digest(refresh_token))
        if locked is None:
            return
        if not _csrf_ok(csrf_header, locked.session):
            raise AppError(ErrorCode.FORBIDDEN, "CSRF token mismatch.")
        await _revoke_family(db, locked.session.family_id, locked.now, RevokeReason.LOGOUT)
