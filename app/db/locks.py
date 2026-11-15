"""The only business-write lock entry point.

Locks the event, the session and the accounts in the documented order,
returns the locked rows and reads ``clock_timestamp()`` after the last
blocking lock. Every API and worker write that can affect participation,
capacity, queue order or session status goes through this module. Locking
these rows anywhere else fails review.

Design: TECHNICAL_DESIGN.md Section 7.1; DATABASE_DESIGN.md Section 4.
"""
