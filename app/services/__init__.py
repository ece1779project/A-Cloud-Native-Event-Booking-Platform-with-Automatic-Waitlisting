"""Transactional command and query services.

A service owns one transaction: it calls ``app.db.locks``, applies the
rules in ``app.domain``, writes state, history and the idempotency receipt,
and builds the response before the transaction exits. It returns the
committed result together with the invalidations to publish.

Services are called by route handlers and by workers and know nothing about
HTTP. They never import ``app.api``, ``app.workers`` or ``app.realtime``;
the caller publishes after the service returns.

Design: TECHNICAL_DESIGN.md Sections 5 and 7.
"""
