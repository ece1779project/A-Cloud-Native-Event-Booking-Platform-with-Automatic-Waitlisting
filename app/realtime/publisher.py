"""Post-commit, best-effort publication of invalidation messages to Redis
Pub/Sub, following the publication matrix. A failure is counted and logged
and never fails the committed command.

Design: TECHNICAL_DESIGN.md Sections 10.3 and 10.5.
"""
