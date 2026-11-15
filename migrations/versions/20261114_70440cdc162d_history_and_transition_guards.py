"""history and transition guards

Database-level guards for rules a row check cannot express
(DATABASE_DESIGN Sections 2.2-2.4 and 3):

* participation_changes and management_actions are append-only.
* A participation keeps its session, account, creation time and queue number,
  keeps its allocation and confirmation times once set, follows only the
  transitions in Section 3, and never leaves a terminal state.
* A session keeps its event, and its schedule and display zone freeze once
  any participation (including a terminal one) exists.
* An auth_sessions row is only ever stamped (DATABASE_DESIGN 2.5): its token,
  family, CSRF digest and absolute expiry never change, and rotation and
  revocation are set at most once.

Autogenerate does not detect triggers; edit this revision by hand.

Revision ID: 70440cdc162d
Revises: 088998119e8b
Create Date: 2026-11-14 19:20:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "70440cdc162d"
down_revision: str | Sequence[str] | None = "088998119e8b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FORBID_HISTORY_MUTATION = """
CREATE FUNCTION forbid_history_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END
$$;
"""

GUARD_PARTICIPATION_UPDATE = """
CREATE FUNCTION guard_participation_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.session_id <> OLD.session_id
       OR NEW.account_id <> OLD.account_id
       OR NEW.created_at <> OLD.created_at
       OR NEW.queue_seq IS DISTINCT FROM OLD.queue_seq THEN
        RAISE EXCEPTION 'participation % identity fields are immutable', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;

    IF OLD.state IN ('CANCELLED', 'EXPIRED') THEN
        RAISE EXCEPTION 'participation % is terminal (%)', OLD.id, OLD.state
            USING ERRCODE = 'check_violation';
    END IF;

    IF OLD.allocated_at IS NOT NULL
       AND (NEW.allocated_at IS DISTINCT FROM OLD.allocated_at
            OR NEW.deadline_at IS DISTINCT FROM OLD.deadline_at) THEN
        RAISE EXCEPTION 'participation % allocation is immutable', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;

    IF OLD.confirmed_at IS NOT NULL
       AND NEW.confirmed_at IS DISTINCT FROM OLD.confirmed_at THEN
        RAISE EXCEPTION 'participation % confirmation time is immutable', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.state <> OLD.state AND (OLD.state, NEW.state) NOT IN (
        ('WAITLISTED', 'OFFERED'),
        ('WAITLISTED', 'CANCELLED'),
        ('HELD', 'CONFIRMED'),
        ('HELD', 'EXPIRED'),
        ('HELD', 'CANCELLED'),
        ('OFFERED', 'CONFIRMED'),
        ('OFFERED', 'EXPIRED'),
        ('OFFERED', 'CANCELLED'),
        ('CONFIRMED', 'CANCELLED')
    ) THEN
        RAISE EXCEPTION 'illegal participation transition % -> %', OLD.state, NEW.state
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END
$$;
"""

GUARD_SESSION_UPDATE = """
CREATE FUNCTION guard_event_session_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.event_id <> OLD.event_id THEN
        RAISE EXCEPTION 'session % cannot move to another event', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;

    IF (NEW.registration_opens_at, NEW.registration_closes_at,
        NEW.starts_at, NEW.ends_at, NEW.time_zone)
       IS DISTINCT FROM
       (OLD.registration_opens_at, OLD.registration_closes_at,
        OLD.starts_at, OLD.ends_at, OLD.time_zone)
       AND EXISTS (SELECT 1 FROM participations p WHERE p.session_id = OLD.id) THEN
        RAISE EXCEPTION 'session % schedule is frozen by existing participation', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END
$$;
"""

GUARD_AUTH_SESSION_UPDATE = """
CREATE FUNCTION guard_auth_session_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.account_id, NEW.family_id, NEW.refresh_token_hash, NEW.csrf_token_hash,
        NEW.created_at, NEW.expires_at, NEW.user_agent_hash)
       IS DISTINCT FROM
       (OLD.account_id, OLD.family_id, OLD.refresh_token_hash, OLD.csrf_token_hash,
        OLD.created_at, OLD.expires_at, OLD.user_agent_hash) THEN
        RAISE EXCEPTION 'auth session % identity fields are immutable', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;

    IF (OLD.rotated_at IS NOT NULL AND NEW.rotated_at IS DISTINCT FROM OLD.rotated_at)
       OR (OLD.revoked_at IS NOT NULL
           AND (NEW.revoked_at, NEW.revoke_reason)
               IS DISTINCT FROM (OLD.revoked_at, OLD.revoke_reason)) THEN
        RAISE EXCEPTION 'auth session % rotation or revocation already stamped', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END
$$;
"""


def upgrade() -> None:
    op.execute(FORBID_HISTORY_MUTATION)
    for table in ("participation_changes", "management_actions"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION forbid_history_mutation()"
        )

    op.execute(GUARD_PARTICIPATION_UPDATE)
    op.execute(
        "CREATE TRIGGER trg_participations_guard_update "
        "BEFORE UPDATE ON participations "
        "FOR EACH ROW EXECUTE FUNCTION guard_participation_update()"
    )

    op.execute(GUARD_SESSION_UPDATE)
    op.execute(
        "CREATE TRIGGER trg_event_sessions_guard_update "
        "BEFORE UPDATE ON event_sessions "
        "FOR EACH ROW EXECUTE FUNCTION guard_event_session_update()"
    )

    op.execute(GUARD_AUTH_SESSION_UPDATE)
    op.execute(
        "CREATE TRIGGER trg_auth_sessions_guard_update "
        "BEFORE UPDATE ON auth_sessions "
        "FOR EACH ROW EXECUTE FUNCTION guard_auth_session_update()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_auth_sessions_guard_update ON auth_sessions")
    op.execute("DROP FUNCTION guard_auth_session_update()")
    op.execute("DROP TRIGGER trg_event_sessions_guard_update ON event_sessions")
    op.execute("DROP FUNCTION guard_event_session_update()")
    op.execute("DROP TRIGGER trg_participations_guard_update ON participations")
    op.execute("DROP FUNCTION guard_participation_update()")
    for table in ("management_actions", "participation_changes"):
        op.execute(f"DROP TRIGGER trg_{table}_append_only ON {table}")
    op.execute("DROP FUNCTION forbid_history_mutation()")
