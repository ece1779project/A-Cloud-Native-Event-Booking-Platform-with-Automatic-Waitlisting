# Development Plan: Waitlist and Workers (Jianye)

Date: 2026-11-17 · Branch: `jianye/waitlist-and-workers` · Based on `main` at `3338979`

## Progress log

- **Coordination (2026-11-16 plan).** Recorded service boundaries, lock and transaction rules, and team ownership in the [technical design](doc/TECHNICAL_DESIGN.md), [database design](doc/DATABASE_DESIGN.md), and [project proposal](doc/Project_Proposal.md).
- **2026-11-17 · Shared types and helpers.** Added `SessionChange`, participation transition and lookup helpers, and test factories that create valid history. The suite passed all 41 tests.
- **2026-11-17 · Promotion and expiration.** Implemented FIFO offers, hold and offer expiration, and registration closure. Expiration and replacement offers share one transaction and version bump. The suite passed all 59 tests.
- **2026-11-17 · Workers and validation.** Added the Compose worker, its expiry, closure, promotion and capacity scans, and their metrics. Consolidated test helpers before review. All 75 tests and Ruff passed. Race tests passed five consecutive runs, and a smoke run verified expiry, replacement offers, capacity-violation reporting and graceful shutdown.

## After my teammates' work lands

**Decline and leave**, once Jiawei merges `core/errors.py`, `services/idempotency.py`, the auth dependency and his first command in `services/participations.py`. Copy his command shell exactly, then add:

- Decline: lock with the caller's account shared, then `expire_if_overdue`. If the offer just expired, commit that and return `409 PARTICIPATION_EXPIRED`, as confirmation does. Otherwise require `OFFERED`, ownership and `now < starts_at`; transition to `CANCELLED` with `OFFER_DECLINED` and the attendee as actor; then `promote_waiters`. Cause `attendee_release`.
- Leave: `end_waiting_if_closed` first. If it ended the entry, commit and return `409 REGISTRATION_CLOSED`. Otherwise require `WAITLISTED`, ownership and `now < starts_at`; transition to `CANCELLED` with `WITHDRAWN`. No promotion. Cause `attendee_release`.
- A repeated command on an attempt already ended by the same action returns `200` with the current result, through the idempotency receipt or the state check.

Review that Jiawei's confirm, cancel, admin cancel and capacity increase call my steps at the sites listed in TECHNICAL_DESIGN 7.3 and 7.4. When `domain/timing.py` lands, replace the private registration-window and offer-deadline helpers in `services/promotion.py`.

**Publisher**, once Yifeng merges it: pass it to the loops instead of the no-op, and check one expiry, one closure and one promotion against the publication matrix.

Switch the worker from `logging.basicConfig` to shared logging when `core/logging.py` is ready.

**Deployment**, with Yifeng: the worker Deployment uses his probes on `WORKER_METRICS_PORT`; the alerts cover `capacity_violations`, the oldest overdue item and scan freshness.

**Joint validation with Jiawei**: the PRD round of 1,000 mixed requests on a capacity-20 session, confirmation racing expiry, withdraw and rejoin at the tail, and an old cancellation replayed after a new booking. For evaluation: promotion latency P95 at most five seconds over at least 100 observations per cause, the backlog cleared within 30 seconds of a restart, two worker replicas running together, and a demonstration with short validity periods set through the environment.
