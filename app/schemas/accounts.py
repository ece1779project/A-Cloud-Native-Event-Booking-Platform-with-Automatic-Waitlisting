"""``AccountCreate``, the account view and ``AdminAccountPatch``.

Design: TECHNICAL_DESIGN.md Section 8.6.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.schemas.common import StrictModel

Password = Annotated[
    str, StringConstraints(strip_whitespace=False, min_length=12, max_length=128)
]


class AccountCreate(StrictModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=100)
    password: Password

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.strip().lower()


class AccountView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    role: str
    disabled: bool
    created_at: datetime

    @classmethod
    def of(cls, account) -> AccountView:
        return cls(
            id=account.id,
            email=account.email,
            display_name=account.display_name,
            role=account.role,
            disabled=account.disabled_at is not None,
            created_at=account.created_at,
        )


class AdminAccountPatch(StrictModel):
    """At least one of ``role`` or ``disabled``; ``ADMIN`` can never be granted
    here (administrators come only from the bootstrap command)."""

    role: Literal["ATTENDEE", "ORGANIZER"] | None = None
    disabled: bool | None = None
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def at_least_one_change(self) -> Self:
        if self.role is None and self.disabled is None:
            raise ValueError("role or disabled is required")
        return self
