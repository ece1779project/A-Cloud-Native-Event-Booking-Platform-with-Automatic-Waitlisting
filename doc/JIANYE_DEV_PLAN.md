# Development Plan: Waitlist and Workers (Jianye)

Date: 2026-11-16 · Branch: `jianye/waitlist-and-workers` · Based on `main` at `5f0b297`

## 1. Where the project stands

Implemented and tested on `main`: settings, the engine, all eight models, both migrations (including the triggers), the lock entry point `app/db/locks.py`, `app/db/participation_store.py`, the state machine in `app/domain/transitions.py`, the health endpoints, and Compose with `migrate`, `api`, `postgres` and `redis`.

Still docstring-only skeletons: every service, every worker, every route module, all schemas, `core/errors.py`, `core/metrics.py`, `core/logging.py`, `core/redis_keys.py`, `domain/timing.py`, the publisher and the gateway. No teammate branch on origin has unmerged work, so nothing of theirs can be built on yet.

## 2. What is mine

The proposal gives me waitlist management, expiration handling, automatic offers, ordered promotion, idempotency and Redis/PostgreSQL consistency. Mapped to the repository:

- Mine, can start now: `domain/timing.py`, `services/promotion.py`, `services/expiration.py`, everything in `workers/`, the worker metrics, and their tests.
- Mine, but blocked or shared: waitlist join, leave and decline (they need authentication, the response envelope, error codes and idempotency receipts), and `services/idempotency.py` (every one of Jiawei's commands calls it, so the owner must be settled first).
- Not mine: authentication, events and sessions, booking, confirmation, cancellation (Jiawei); publisher, gateway, frontend, Kubernetes, monitoring stack (Yifeng).

## 3. Rules that keep this work from being redone

1. Depend only on merged code: the database layer and `domain`. Nothing below imports authentication, HTTP, schemas, error classes or Redis.
2. Write business logic as in-transaction steps with the shape `step(db, scope, *, correlation_id, ...) -> list[Transition]`. A step never opens or commits a transaction, never bumps the session version and never publishes. A thin wrapper owns the transaction for the workers. If Jiawei chooses a different transaction convention, only the wrappers change.
3. Tests build rows with `tests/factories.py` and never go through the booking service.
4. Publication is a callback injected into the worker loop, with a no-op default until the publisher exists.
5. Change shared files (`participation_store.py`, `factories.py`, `config.py`, `compose.yaml`, `pyproject.toml`) additively only.

## 4. Step 0: agree on the seams before coding

Send one short message to the team and wait for a yes on each point. These are the only places where my code touches theirs.

- Result type. Every service returns a `SessionChange` (Step 2). Jiawei's commands return the same type; Yifeng's publisher takes it as input.
- One version bump per transaction. Section 10.5 requires contiguous versions, so the transaction owner bumps once, after all steps have run. Steps never bump.
- Call sites in Jiawei's code. Every command on an existing participation first calls `expire_if_overdue` and `end_waiting_if_closed`. Cancel, decline, administrator cancel, a capacity increase and a confirmation that met an expired attempt all call `promote_waiters` before commit.
- Ownership of `domain/timing.py` (I propose mine, since booking and promotion both need it), `services/idempotency.py`, and the join, leave and decline commands.
- With Yifeng: how the worker Pod is probed and scraped. I propose `/metrics` on a new `WORKER_METRICS_PORT` plus a last-scan timestamp gauge.

## 5. Work I can finish now

### Step 1: time rules

File: `app/domain/timing.py`. Add `RegistrationStatus` (`NOT_OPEN`, `OPEN`, `CLOSED`, matching `openapi.yaml`) to `app/domain/enums.py`.

Functions, all pure and all taking the database time as an argument: `registration_status(opens_at, closes_at, now)`; `registration_open(event_status, session_status, opens_at, closes_at, now)`, which also requires both statuses to be `PUBLISHED`; `effective_deadline(allocated_at, validity_seconds, registration_closes_at)`, the earlier of the two; `is_overdue(deadline_at, now)`, true when `now >= deadline_at`.

Test: `tests/unit/test_timing.py`. Cover the inclusive open instant, the exclusive close instant, the deadline capped by registration close, and equality counting as overdue.

### Step 2: shared result type, transition helper, factories

New file `app/domain/changes.py` with two frozen dataclasses. `Transition` holds `participation_id`, `account_id`, `from_state`, `to_state`, `reason_code`, `occurred_at` and `deadline_at`. `SessionChange` holds `event_id`, `session_id`, `version`, `cause` and a tuple of transitions. `cause` is a `ChangeCause` enum whose values are exactly the `session.changed` reasons in Section 10.1, so the publisher can use it verbatim.

Additions to `app/db/participation_store.py`:

- `apply_transition(db, participation, to_state, *, reason, now, correlation_id, actor_id, deadline_at=None)` checks `can_transition`, sets the state and its timestamp fields, calls `record_change` and returns a `Transition`.
- `overdue_attempts(db, session_id, now)` and `active_attempts(db, session_id, states)` return rows in a stable order.
- `build_change(db, session, cause, transitions)` bumps the version once when the list is non-empty and returns the `SessionChange`; otherwise it returns `None`.

Additions to `tests/factories.py`: `make_offered`, `make_confirmed`, an `overdue=True` option (allocated ten minutes ago, deadline one minute ago, which still satisfies `deadline_at > allocated_at`), a `make_session` option for an already-closed registration window, and `make_account(disabled=True)`.

Tests: extend `tests/integration/test_locks_and_store.py` for the three new store functions, including an illegal transition being refused before it reaches the trigger.

Open this as its own small pull request and merge it early, so Jiawei builds on the same types.

### Step 3: promotion

File: `app/services/promotion.py`.

`promote_waiters(db, scope, *, offer_validity_seconds, correlation_id)` is the in-transaction step. It reads the counts once, then loops while a seat is free: take `queue_head`, lock that account with `lock_queue_head_account`, and use the fresh time that lock returns to recheck that registration is open. If it is not, stop. A disabled account's entry ends as `CANCELLED` with `ACCOUNT_DISABLED` and the loop continues. Otherwise the entry becomes `OFFERED` with `allocated_at` set to that time and the deadline from `effective_deadline`. Stop after `MAX_OFFERS_PER_TRANSACTION` offers, and also after 50 queue heads examined, so a run of disabled accounts cannot stretch the transaction.

`promote_session(session_factory, session_id, settings)` is the wrapper for the promotion scan: open a transaction, `lock_session_scope`, run the step, `build_change`, commit, return the change.

Tests in `tests/integration/test_promotion.py`: one seat goes to the lowest `queue_seq`; several seats go out in order; a disabled head is skipped with its reason and the next waiter gets the offer; nothing happens when there is no free seat, registration is closed, the session is a draft or the event is cancelled; the deadline is capped at registration close; 25 seats produce 20 offers and leave five; history rows carry `PROMOTED`, a null actor and the shared correlation ID; the version rises by exactly one.

### Step 4: expiration and closure

File: `app/services/expiration.py`.

In-transaction steps: `expire_if_overdue(db, scope, participation, *, correlation_id)` for Jiawei's commands; `end_waiting_if_closed(...)` for a late attendee action on a waiting entry; `expire_due(db, scope, *, correlation_id)`, which expires every overdue hold and offer in the locked session with `HOLD_EXPIRED` or `OFFER_EXPIRED`; `close_registration(db, scope, *, correlation_id)`, which expires holds and offers, ends waiters with `REGISTRATION_CLOSED`, leaves confirmed rows alone and never promotes.

Wrappers: `expire_session(session_factory, session_id, settings)` runs `expire_due`, then `promote_waiters`, then `build_change`. `close_session(...)` runs `close_registration` with the cause `registration_closed`.

Tests in `tests/integration/test_expiration.py`: an overdue hold and an overdue offer each expire with the right reason; a row that is not yet due is untouched; expiry and the next offer land in one commit with one correlation ID; an expired offer does not rejoin the queue; a stale candidate is a no-op that leaves the version unchanged; closure ends each state correctly and issues no offer; a second closure run changes nothing.

### Step 5: worker process

`app/workers/runner.py`: a `Scan` with a name, `find_candidates(db, limit)` and `process(candidate)`, and `run_scan_loop(scan, *, interval, batch_size, stop, publish)`. Each cycle reads candidates in a short read-only transaction, processes them one by one, passes each returned `SessionChange` to `publish`, and then waits for the rest of the interval or the stop signal. A failure on one candidate is logged and counted and the loop moves on. A lock or statement timeout (`is_retryable_timeout`) is left for the next cycle. A failure of the scan itself backs off exponentially up to 30 seconds.

Scan modules, each holding only its candidate query and a call to the matching wrapper:

- `expiry.py`: distinct session IDs with a due `HELD` or `OFFERED` row, oldest deadline first. The same query's count and oldest age feed the backlog metrics.
- `closure.py`: published sessions past `registration_closes_at` that still have `HELD`, `OFFERED` or `WAITLISTED` rows.
- `promotion.py`: open, published sessions with a waiter and occupancy below capacity. This is the safety net for more than 20 freed seats and for any call site that was missed.
- `invariant.py`: runs `capacity_violations`, sets the gauge and logs each offending session at error level.

`app/workers/__main__.py`: load settings, create the engine, start the metrics server, start the four loops, and on `SIGTERM` or `SIGINT` set the stop event, let the current transaction finish and exit within 20 seconds.

`app/core/metrics.py`: add only the worker metrics from Section 14 (`worker_scan_duration_seconds`, `worker_candidates_total`, `worker_overdue_items`, `worker_oldest_overdue_seconds`, `waitlist_promotion_latency_seconds`, `capacity_violations`) and a last-scan timestamp gauge. This needs `uv add prometheus-client`; if a teammate adds a dependency at the same time, resolve `uv.lock` by running `uv lock` again.

Also: `worker_metrics_port` in `app/core/config.py`, and a `worker` service in `compose.yaml` running `python -m app.workers` after `migrate`.

Tests: `tests/unit/test_runner.py` with a fake scan (continues after a candidate error, backs off after a scan error, stops promptly on the stop event), and `tests/integration/test_worker_scans.py` (each candidate query returns exactly the sessions it should; one cycle clears a prepared backlog; the invariant scan reports a violation inserted through the factories).

### Step 6: concurrency and recovery tests

File: `tests/concurrency/test_worker_races.py`. Every test ends with `capacity_violations(db) == []`.

- Two `expire_session` calls on the same session at once: one transition, one offer.
- Ten `promote_session` calls racing for three seats and eight waiters: offers go to queue numbers 1 to 3, with no duplicates.
- A holder keeps the session lock across a deadline while two expiry calls wait: exactly one `EXPIRED` row afterwards.
- Expiry racing closure on a session at its close instant: each row ends once, and no offer is created.
- Recovery (FR-07.11): prepare overdue rows, then start a fresh runner; the backlog clears and original deadlines are unchanged.

### Step 7: wrap up

Update `TECHNICAL_DESIGN.md`: Section 2 status, the new `domain/changes.py` in Section 5, and `WORKER_METRICS_PORT` in Section 13.5. Keep three pull requests: Step 1 and 2, then Step 3 and 4, then Step 5 and 6.

To run everything locally:

```bash
docker run -d --rm --name booking-test-db -p 55432:5432 -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=booking_test postgres:18
```

```bash
TEST_DATABASE_URL=postgresql+asyncpg://test:test@127.0.0.1:55432/booking_test uv run pytest
```

## 6. After my teammates' parts land

When Jiawei's authentication, error codes, envelopes and idempotency exist: implement whichever of join, leave and decline are assigned to me; review that his commands call my steps at the agreed sites; then validate jointly through the real services. That means the PRD round of 1,000 mixed requests on a capacity-20 session, confirmation racing expiry, withdraw and rejoin at the tail, and an old cancellation replayed after a new booking.

When Yifeng's publisher exists: pass it to the runner in place of the no-op and check the messages against the publication matrix. Then the worker Deployment, its probes, the Prometheus scrape, and the alerts on `capacity_violations` and the oldest overdue item.

For evaluation: promotion latency P95 of five seconds or less over at least 100 observations per cause, the backlog cleared within 30 seconds of a restart, two worker replicas running together, and the demonstration with shortened validity periods set through the environment.

## 7. Details that are easy to miss

- Recheck registration with the fresh time after every queue-head account lock. An offer written at or after close would break the `deadline_at > allocated_at` check.
- A seat freed after registration close stays empty. Closure never promotes.
- Workers are idempotent by state, not by receipts. They write no `request_results` row, use a null `actor_id`, and create one new correlation ID per transaction.
- The first version uses no `SKIP LOCKED` at all. Two workers on one session queue on the lock and the second finds nothing to do. Add it across sessions only if metrics show contention.
- Measure promotion latency after commit as the offer's `occurred_at` minus the expired row's `deadline_at`. The same figure can be recomputed for the report from `participation_changes` by joining on the correlation ID.
- Nothing touches Redis inside a transaction. Publication runs after commit, and its failure is counted, never raised.
- A trigger rejection in a test means the service missed a check. Fix the service, not the test.
- Until `core/logging.py` exists, use `logging.getLogger(__name__)` and one `basicConfig` call in `__main__`, so the later switch is one line.
- Three choices here are mine and worth a quick confirmation in Step 0: a null actor on every promotion, `ACCOUNT_DISABLED` as the reason for a skipped disabled waiter, and expiring all overdue rows of a session in one transaction.
