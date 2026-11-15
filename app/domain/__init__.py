"""Pure business vocabulary and rules.

Plain Python with no I/O and no imports from the rest of ``app``, so models,
schemas, services, workers and the gateway can all share it and unit tests
can cover it without a database.

Design: DATABASE_DESIGN.md Section 3.
"""
