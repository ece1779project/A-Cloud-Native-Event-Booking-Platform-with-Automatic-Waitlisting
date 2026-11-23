"""Creates the first administrator from ``BOOTSTRAP_ADMIN_EMAIL`` and
``BOOTSTRAP_ADMIN_PASSWORD``: ``python -m app.cli.bootstrap_admin``.

Idempotent: when the email already exists nothing changes (neither role nor
password), so deploy jobs can run it on every release. The password is never
printed.

Design: TECHNICAL_DESIGN.md Sections 8.2 and 13.5.
"""

import asyncio
import sys

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.engine import (
    UNIQUE_VIOLATION,
    create_engine,
    create_session_factory,
    sqlstate,
)
from app.domain.enums import Role
from app.models.account import Account

MIN_PASSWORD_LENGTH = 12


async def bootstrap(
    factory: async_sessionmaker[AsyncSession], *, email: str, password: str
) -> bool:
    """Returns True when an administrator was created, False when the email exists."""
    email = email.strip().lower()
    password_hash = hash_password(password)
    try:
        async with factory.begin() as db:
            existing = await db.scalar(
                select(Account).where(func.lower(Account.email) == email)
            )
            if existing is not None:
                return False
            db.add(
                Account(
                    email=email,
                    display_name="Administrator",
                    password_hash=password_hash,
                    role=Role.ADMIN.value,
                )
            )
    except IntegrityError as exc:
        if sqlstate(exc) == UNIQUE_VIOLATION:
            return False
        raise
    return True


async def main() -> int:
    settings = get_settings()
    email = (settings.bootstrap_admin_email or "").strip().lower()
    password = settings.bootstrap_admin_password
    if (
        not email
        or password is None
        or len(password.get_secret_value()) < MIN_PASSWORD_LENGTH
    ):
        print(
            "BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD "
            f"(at least {MIN_PASSWORD_LENGTH} characters) are required",
            file=sys.stderr,
        )
        return 2

    engine = create_engine(settings)
    try:
        created = await bootstrap(
            create_session_factory(engine),
            email=email,
            password=password.get_secret_value(),
        )
    finally:
        await engine.dispose()
    print(
        f"created administrator {email}"
        if created
        else f"account {email} already exists; nothing to do"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
