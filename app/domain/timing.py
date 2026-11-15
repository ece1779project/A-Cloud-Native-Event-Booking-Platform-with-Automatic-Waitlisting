"""Time rules as pure functions of a supplied database time: registration
window status, the effective deadline of a new hold or offer, and whether
an allocation is overdue.

Callers pass ``clock_timestamp()`` read after their locks; nothing here
reads a clock.

Design: TECHNICAL_DESIGN.md Sections 6.1 and 6.3.
"""
