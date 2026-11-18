"""One function per Redis key and Pub/Sub channel. No other module spells out
a Redis name by hand.

Design: TECHNICAL_DESIGN.md Section 10.5.
"""

import uuid

PREFIX = "seatflow:v1:"


def rate_limit_write(account_id: uuid.UUID, window: int) -> str:
    """Registration writes by one account in one fixed one-minute window."""
    return f"{PREFIX}rl:write:{account_id}:{window}"


def rate_limit_login(ip: str, window: int) -> str:
    """Login attempts from one normalized source address in one window."""
    return f"{PREFIX}rl:login:{ip}:{window}"
