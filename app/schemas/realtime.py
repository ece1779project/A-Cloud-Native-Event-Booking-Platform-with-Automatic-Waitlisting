"""One model per WebSocket message type and one enum per ``reason`` list,
plus the ticket request and response. Also the internal ``SessionChange``
result, which is not a WebSocket message. No other module spells out a
message body by hand.

Design: TECHNICAL_DESIGN.md Sections 10.1, 10.3, and 10.5.
"""
