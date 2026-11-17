"""Post-commit, best-effort publication of invalidation messages to Redis
Pub/Sub. Callers pass the ``SessionChange`` values of one commit; ``None``
is not published. A failure is counted and logged and never fails the
committed command.

Design: TECHNICAL_DESIGN.md Sections 10.3 and 10.5.
"""
