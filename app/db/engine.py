"""One ``AsyncEngine`` and ``async_sessionmaker`` per process, with UTC
connections, pool bounds from settings, and the lock, statement and
idle-in-transaction timeouts.

Design: TECHNICAL_DESIGN.md Sections 7.1 and 13.4; DATABASE_DESIGN.md Section 6.
"""
