"""FastAPI dependencies shared by all routes.

- Database session factory and Redis client for the request.
- Current account: verify the access token, then load the account and reject
  a disabled account or an insufficient current role.
- ``Idempotency-Key`` header parsing for business commands.
- Rate-limit checks that run before any business transaction.
- Request context: request ID and server time for the response ``meta``.

Design: TECHNICAL_DESIGN.md Sections 7.5, 8.1, 9.3 and 9.5.
"""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session
