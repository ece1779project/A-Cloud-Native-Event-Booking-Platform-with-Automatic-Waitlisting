"""Background worker process.

A scan only selects candidates. Each candidate is handled in a new
transaction through the same services and locks as the API, so a stale
candidate becomes a no-op.

Design: TECHNICAL_DESIGN.md Section 11.
"""
