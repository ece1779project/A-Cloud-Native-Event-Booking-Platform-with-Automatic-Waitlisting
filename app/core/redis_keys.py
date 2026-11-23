"""The single source for Redis keys and Pub/Sub channel names.

Design: TECHNICAL_DESIGN.md Section 10.5.
"""

import uuid

PREFIX = "ticketbooking:v1:"


def rate_limit_write(account_id: uuid.UUID, window: int) -> str:
    return f"{PREFIX}rl:write:{account_id}:{window}"


def rate_limit_login(ip: str, window: int) -> str:
    return f"{PREFIX}rl:login:{ip}:{window}"


# Descriptive aliases retained for the real-time contract tests.
registration_write_rate_limit = rate_limit_write
login_rate_limit = rate_limit_login


def websocket_ticket(digest: str) -> str:
    return f"{PREFIX}ws:ticket:{digest}"


def session_channel(session_id: uuid.UUID) -> str:
    return f"{PREFIX}ps:session:{session_id}"


def account_channel(account_id: uuid.UUID) -> str:
    return f"{PREFIX}ps:account:{account_id}"


def management_channel(event_id: uuid.UUID) -> str:
    return f"{PREFIX}ps:event:{event_id}:management"


def pubsub_pattern() -> str:
    return f"{PREFIX}ps:*"


def logical_channel(redis_channel: str) -> tuple[str, uuid.UUID] | None:
    """Return the browser channel kind and resource ID for a Pub/Sub name."""
    channel_prefix = f"{PREFIX}ps:"
    if not redis_channel.startswith(channel_prefix):
        return None
    parts = redis_channel.removeprefix(channel_prefix).split(":")
    try:
        if len(parts) == 2 and parts[0] in {"session", "account"}:
            return parts[0], uuid.UUID(parts[1])
        if len(parts) == 3 and parts[0] == "event" and parts[2] == "management":
            return "management", uuid.UUID(parts[1])
    except ValueError:
        return None
    return None
