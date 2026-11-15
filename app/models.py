"""Import every mapped model so Base.metadata is complete (used by Alembic)."""

from app.infra.db import Base
from app.modules.audit.models import ManagementAction
from app.modules.auth.models import Account
from app.modules.booking.models import Participation, ParticipationChange, RequestResult
from app.modules.events.models import Event, EventSession

__all__ = [
    "Account",
    "Base",
    "Event",
    "EventSession",
    "ManagementAction",
    "Participation",
    "ParticipationChange",
    "RequestResult",
]
