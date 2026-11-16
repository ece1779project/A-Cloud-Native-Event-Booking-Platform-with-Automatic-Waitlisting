"""Pydantic contracts for REST and WebSocket messages.

Every write schema rejects unknown fields. Changing a shape here changes
the public contract and its contract tests.

Design: TECHNICAL_DESIGN.md Sections 8.6 and 10.1.
"""
