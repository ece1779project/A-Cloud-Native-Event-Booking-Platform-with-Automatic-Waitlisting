"""The single Redis pattern subscription and in-memory routing from a Redis
channel to subscribed connections, including the pre-delivery checks and
``system.resync_required`` after a Redis reconnect.

Design: TECHNICAL_DESIGN.md Sections 10.2 and 10.5.
"""
