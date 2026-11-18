"""``LoginRequest`` and ``TokenResponse``.

Design: TECHNICAL_DESIGN.md Section 8.6.
"""

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.accounts import AccountView
from app.schemas.common import StrictModel


class LoginRequest(StrictModel):
    email: EmailStr
    password: str = Field(max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize(cls, v: str) -> str:
        return v.strip().lower()


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    account: AccountView
