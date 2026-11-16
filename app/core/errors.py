"""Application exception hierarchy and the stable error codes with their HTTP
status and ``retryable`` flag.

Services raise these; ``app.api.exception_handlers`` renders them.

Design: TECHNICAL_DESIGN.md Section 8.5.
"""
