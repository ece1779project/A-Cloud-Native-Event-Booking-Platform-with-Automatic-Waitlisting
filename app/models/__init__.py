"""SQLAlchemy models, one module per table.

Every model is imported here so that ``Base.metadata`` is complete for
Alembic autogeneration. Models hold columns, constraints and indexes only;
they contain no business logic and never publish messages.

Design: DATABASE_DESIGN.md Section 2.
"""

from app.db.base import Base
from app.models.account import Account
from app.models.auth_session import AuthSession
from app.models.event import Event
from app.models.event_session import EventSession
from app.models.management_action import ManagementAction
from app.models.participation import Participation
from app.models.participation_change import ParticipationChange
from app.models.request_result import RequestResult

__all__ = [
    "Account",
    "AuthSession",
    "Base",
    "Event",
    "EventSession",
    "ManagementAction",
    "Participation",
    "ParticipationChange",
    "RequestResult",
]
