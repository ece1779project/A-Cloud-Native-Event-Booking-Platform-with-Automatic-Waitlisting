"""The promote steps, run inside any transaction that frees a seat or raises
capacity and by the promotion scan. Always takes the true lowest
``queue_seq``; never uses ``SKIP LOCKED`` within a session.

Design: TECHNICAL_DESIGN.md Section 7.4; DATABASE_DESIGN.md Section 4.
"""
