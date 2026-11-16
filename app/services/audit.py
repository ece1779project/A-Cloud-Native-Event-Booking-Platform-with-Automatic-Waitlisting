"""Writer for ``management_actions``, called inside the transaction that
makes the change.

Participation history (``participation_changes``) is written by
``app.db.participation_store.record_change``, not here.

Design: DATABASE_DESIGN.md Section 2.4.
"""
