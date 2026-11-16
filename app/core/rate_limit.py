"""Fixed one-minute-window rate limiter backed by Redis, with the in-process
fallback each API replica uses while Redis is unavailable.

Design: TECHNICAL_DESIGN.md Sections 9.5 and 10.5.
"""
