"""Writer for ``management_actions``, called inside the transaction that
makes the change.

Participation history (``participation_changes``) is written by
``app.db.participation_store.record_change``, not here.

Design: DATABASE_DESIGN.md Section 2.4.
"""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ManagementActionType
from app.models.management_action import ManagementAction


async def record_action(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID,
    action: ManagementActionType,
    reason: str,
    occurred_at: datetime,
    correlation_id: uuid.UUID,
    target_account_id: uuid.UUID | None = None,
    target_event_id: uuid.UUID | None = None,
    target_session_id: uuid.UUID | None = None,
    target_participation_id: uuid.UUID | None = None,
) -> ManagementAction:
    """Append one audit row in the caller's transaction, so the change and its
    audit commit or roll back together. Exactly one target is set (the
    ``exactly_one_target`` check enforces it)."""
    row = ManagementAction(
        actor_id=actor_id,
        action=action.value,
        reason=reason,
        occurred_at=occurred_at,
        correlation_id=correlation_id,
        target_account_id=target_account_id,
        target_event_id=target_event_id,
        target_session_id=target_session_id,
        target_participation_id=target_participation_id,
    )
    db.add(row)
    await db.flush()
    return row
