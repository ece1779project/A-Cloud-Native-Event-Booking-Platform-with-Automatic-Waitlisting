# Development Plan: Waitlist and Workers (Jianye)

Date: 2026-11-17 · Branch: `jianye/waitlist-and-workers` · Based on `main` at `3338979`

This version replaces the 2026-11-16 plan. Step 0 of that plan is done: the team recorded the seams in TECHNICAL_DESIGN 1.3, DATABASE_DESIGN 1.4 and the ownership table in Project_Proposal Section 3.2. This plan follows those documents. Two doc edits made with this plan: worker metric definitions move to me because the scan loops set them (Yifeng keeps the worker HTTP server, probes and alerts), and `INELIGIBLE` is reserved because an enabled account is the only eligibility rule.

## 1. Where the project stands

Already merged into `main`, with tests: settings (including `WORKER_METRICS_PORT`), the engine and `db_now`, all eight models, both migrations with their triggers, `db/locks.py`, `db/participation_store.py` (counts, queue head, position, queue number, `bump_version`, `record_change`, `capacity_violations`), `domain/enums.py`, `domain/transitions.py`, the health endpoints, and Compose with `migrate`, `api`, `postgres` and `redis`.

Still skeletons: every service, every worker, every route, all schemas, `domain/timing.py`, `core/errors.py`, `core/metrics.py`, `core/logging.py`, the publisher and the gateway. Jiawei has an unmerged `core/errors.py` on `origin/feat/database`; nothing here depends on it.

## 2. Rules that keep this work from being redone

1. Import only merged code: `db`, `models`, `domain`, `core/config`, `core/constants`. Nothing I write before Jiawei's work lands imports `core/errors`, `services/idempotency`, `domain/timing`, auth or HTTP.
2. Steps have the shape `step(db, scope, ..., *, correlation_id) -> list[ParticipationTransition]`. A step never commits, never bumps the version, never publishes and never raises an API error. Jiawei calls the same steps from his commands.
3. Until `domain/timing.py` exists, `services/promotion.py` keeps two private helpers, `_registration_open` and `_offer_deadline`, with a comment to switch to `domain.timing`. Switching later is a two-line change. I do not write `timing.py`.
4. Tests build rows with `tests/factories.py` and never go through the booking service.
5. Publication is a callback passed to the worker loop, a no-op until Yifeng's publisher exists.
6. Shared files (`participation_store.py`, `factories.py`, `compose.yaml`, `pyproject.toml`, `core/metrics.py`, `schemas/realtime.py`) change additively only, appended at the end of the file to keep merges clean.
7. Phases 1 and 2 define the types and signatures Jiawei builds on, so they come first.

## 3. Phase 1: shared types and helpers (done)

Status, 2026-11-17: implemented and passing (41 tests, `ruff check` clean). Two changes from the original plan, both reflected below: the factories now write the history rows a real service would write, and the unit test file is `tests/unit/test_session_change.py`.

### 3.1 `SessionChange` in `app/schemas/realtime.py`

Write only this part of the file, as frozen Pydantic models, exactly as Section 10.3 defines it:

- `ChangeCause` (StrEnum): `booking_hold`, `waitlist_join`, `confirmation`, `attendee_release`, `admin_participation_cancel`, `expiration`, `promotion`, `registration_closed`, `capacity_changed`, `session_published`, `event_published`, `session_cancelled`, `event_cancelled`.
- `ParticipationChangedReason` (StrEnum): `hold_created`, `waitlist_joined`, `offer_created`, `confirmed`, `cancelled`, `expired`.
- `REASON_BY_STATE`: a dict from the new `ParticipationState` to that reason. The mapping is one-to-one, so every service derives the reason the same way.
- `ParticipationTransition`: `participation_id`, `account_id`, `reason`.
- `SessionChange`: `event_id`, `session_id`, `version`, `cause`, `transitions` (a tuple, possibly empty).

Leave the other message models and reason enums to Yifeng. Add a short comment that the section above is the internal result and the section below is the wire contract.

### 3.2 Additions to `app/db/participation_store.py`

- `apply_transition(db, participation, to_state, *, reason_code, now, correlation_id, actor_id, deadline_at=None) -> None`. Raises `ValueError` when `can_transition` refuses, so a bug never reaches the trigger. Sets the fields the target state needs: `allocated_at` and `deadline_at` for `OFFERED`, `confirmed_at` for `CONFIRMED`, `ended_at` and `end_reason` for `CANCELLED` and `EXPIRED`. Then calls `record_change`. It decides nothing else.
- `due_attempts(db, session_id, now) -> list[Participation]`: `HELD` and `OFFERED` rows with `deadline_at <= now`, ordered by `deadline_at, id`.
- `attempts_in_states(db, session_id, states) -> list[Participation]`: ordered by `queue_seq NULLS FIRST, id`, used by closure.

Both reads use `populate_existing` like the existing queries.

### 3.3 Additions to `tests/factories.py`

`make_account(disabled=False)`; `make_session(status=..., window="open" | "closed" | "not_open")`; `make_scope(window=...)`; `make_held(overdue=False)`; `make_offered(queue_seq, overdue=False)`; `make_confirmed`. An overdue row is allocated ten minutes ago with a deadline one minute ago, which satisfies `deadline_at > allocated_at`. A closed window keeps `opens_at < closes_at <= starts_at`.

Every participation builder also writes its history: `BOOKED` for a hold, `ENQUEUED` for a waiter, `ENQUEUED` then `PROMOTED` for an offer, `BOOKED` then `PAYMENT_CONFIRMED` for a confirmed row. Without it, the first `apply_transition` on a factory row would be history change 1 with a non-null source state, which the `first_has_no_source` check rejects. Two existing tests that inserted change 1 by hand now rely on the factory instead (`test_history_tables_are_append_only`, `test_store_helpers`).

### 3.4 Tests

Extended `tests/integration/test_locks_and_store.py`: each target state gets the right fields and history row, and the trigger accepts the commit; an illegal transition or a misplaced `deadline_at` raises `ValueError` and writes nothing; `due_attempts` includes a row exactly at its deadline and excludes it a second earlier; `attempts_in_states` puts unqueued rows first. Added `tests/unit/test_session_change.py`: the enum values match the design, `REASON_BY_STATE` covers all six states one to one, and the models are frozen.

## 4. Phase 2: promotion and expiration (done)

Status, 2026-11-17: implemented and passing (59 tests in the suite, `ruff check` clean). Changes from the original plan, all reflected below: the transaction owners take `offer_validity_seconds` instead of `settings`; the version helper is public as `build_session_change`; `ParticipationTransition.of(row)` builds a transition from a changed row; `expire_session` promotes only when something expired; promotion latency moves to Phase 3 with its metric; a shared `capacity_invariant` fixture in `tests/conftest.py` runs the invariant query after each test that opts in.

### 4.1 `app/services/promotion.py`

`promote_waiters(db, scope, *, offer_validity_seconds, correlation_id) -> list[ParticipationTransition]`

1. Read `session_counts` once. While occupied is below capacity and fewer than `MAX_OFFERS_PER_TRANSACTION` offers were made: take `queue_head`; stop if none.
2. `lock_queue_head_account` and use the fresh `now` it returns. Recheck that registration is open at that time (both statuses `PUBLISHED`, `opens_at <= now < closes_at`); stop if not.
3. A disabled account: `CANCELLED` with `ACCOUNT_DISABLED`, null actor; continue with the next head.
4. Otherwise: `OFFERED`, `allocated_at = now`, `deadline_at = min(now + validity, closes_at)`, reason `PROMOTED`, null actor. Count the seat as occupied and loop.

### 4.2 `app/services/expiration.py`

Steps, all using `scope.now` and a null actor:

- `expire_if_overdue(db, scope, participation, *, correlation_id)`: a due `HELD` or `OFFERED` row becomes `EXPIRED` with `HOLD_EXPIRED` or `OFFER_EXPIRED`. Jiawei calls this first in confirm, cancel and admin cancel; I call it in decline.
- `end_waiting_if_closed(db, scope, participation, *, correlation_id)`: a `WAITLISTED` row at or after `closes_at` becomes `CANCELLED` with `REGISTRATION_CLOSED`.
- `expire_due(db, scope, *, correlation_id)`: every row from `due_attempts`.
- `close_registration(db, scope, *, correlation_id)`: due holds and offers expire with their own reasons, waiters end with `REGISTRATION_CLOSED`, confirmed rows stay, no promotion. Does nothing before `closes_at`.

Transaction owners, each `async def name(session_factory, session_id, ...) -> SessionChange | None`:

- `expire_session(..., *, offer_validity_seconds)`: lock, `expire_due`, then `promote_waiters` if anything expired, cause `expiration`.
- `close_session`: lock, `close_registration`, cause `registration_closed`.
- `promote_session(..., *, offer_validity_seconds)`: lock, `promote_waiters`, cause `promotion`.

Each opens `session_factory.begin()`, calls `lock_session_scope`, creates one `uuid.uuid7()` correlation ID, runs its steps, and, only if transitions is non-empty, calls `bump_version` once and builds the `SessionChange`. An empty result returns `None` and leaves the version alone. `build_session_change(db, scope, cause, transitions)` holds that logic; Jiawei can import it for his commands that change at least one participation.

Each step returns `ParticipationTransition.of(row)` for every row it changed. `close_registration` reuses `expire_if_overdue` and `end_waiting_if_closed` row by row, so a late attendee command and the worker write the same outcome.

### 4.3 Tests

`tests/integration/test_promotion.py`: one seat goes to the lowest `queue_seq`; several seats go out in order; a disabled head ends with `ACCOUNT_DISABLED` and the next waiter is offered; nothing happens with no free seat, a closed window, a draft session or a cancelled event; the deadline is capped at `closes_at`; 25 free seats give 20 offers and leave five waiters; history rows carry `PROMOTED`, a null actor and one correlation ID.

`tests/integration/test_expiration.py`: an overdue hold and an overdue offer each expire with the right reason; a row not yet due is untouched; expiry and the next offer share one commit, one correlation ID and one version bump; the expired offer does not rejoin the queue; a stale candidate returns `None` with the version unchanged; closure ends each state correctly and issues no offer; a second closure run returns `None`; `expire_if_overdue` and `end_waiting_if_closed` each return an empty list on a row that is still valid.

Every test ends with `capacity_violations(db) == []`.

## 5. Phase 3: worker process and race tests (done)

Status, 2026-11-17: implemented and passing (75 tests in the suite, `ruff check` clean, concurrency tests stable over five repeated runs). A smoke run of `python -m app.workers` against the test database expired a seeded hold, offered the seat, reported a capacity violation and stopped cleanly on `SIGTERM`. Changes from the original plan, reflected below: a scan opens its own read transaction, so `find_candidates(limit)` takes no `db` and the runner never touches the database; the runner exposes `run_scan_once` for tests; the invariant scan does its work in `find_candidates` and returns no candidates; candidate outcomes are `changed`, `noop`, `busy` and `error`; factory `minutes` accepts fractions for second-scale deadlines.

### 5.1 Metrics

Run `uv add prometheus-client`. In `app/core/metrics.py`, append only the worker metrics from Section 14: `worker_scan_duration_seconds` (histogram, label `scan`), `worker_candidates_total` (counter, labels `scan` and `outcome`), `worker_overdue_items` and `worker_oldest_overdue_seconds` (gauges), `worker_last_scan_timestamp_seconds` (gauge, label `scan`), `waitlist_promotion_latency_seconds` (histogram, label `cause`), and `capacity_violations` (gauge). If a teammate changed `uv.lock` at the same time, resolve it by running `uv lock` again.

Promotion latency: inside `expire_session`, after the steps, read each expired row's `deadline_at` and each new offer's `allocated_at` from the identity map (`db.get` issues no query), pair offers in order with the expired deadlines sorted ascending, and observe the differences with `cause="expiration"` only after the commit succeeds.

### 5.2 `app/workers/runner.py`

A `Scan` has a `name`, `find_candidates(limit)` (which opens its own short read transaction) and `process(candidate) -> SessionChange | None`. `run_scan_once(scan, *, batch_size, publish, stop=None)` is one pass: read candidates, process them one at a time, pass each non-`None` result to `publish`, record the duration and set the last-scan gauge. `run_scan_loop(scan, *, interval, batch_size, stop, publish=publish_nothing, max_backoff=30)` repeats passes and waits for the rest of the interval or for `stop`.

A failing candidate is logged, counted with `outcome="error"`, and skipped; a lock or statement timeout (`is_retryable_timeout`) counts as `outcome="busy"` and is retried next cycle. A failing candidate query is a dependency failure: back off exponentially to 30 seconds and leave the gauge unchanged. A `publish` failure is logged and never stops the loop.

### 5.3 Scan modules

Each holds a candidate query and a call to one transaction owner.

- `expiry.py`: distinct session IDs with a due `HELD` or `OFFERED` row, oldest deadline first, limited to the batch. The same query sets `worker_overdue_items` and `worker_oldest_overdue_seconds`.
- `closure.py`: `PUBLISHED` sessions with `closes_at <= now` that still have `HELD`, `OFFERED` or `WAITLISTED` rows.
- `promotion.py`: open published sessions with a waiter and occupancy below capacity. This is the safety net for more than 20 freed seats and for any missed call site.
- `invariant.py`: runs `capacity_violations` inside `find_candidates`, sets the gauge, logs each offending session at error level, and returns no candidates.

### 5.4 `app/workers/__main__.py` and Compose

Load settings, create the engine and session factory, call `logging.basicConfig` (switch to `core/logging.py` when it exists), build the four scans, and run their loops as tasks with a no-op `publish`. On `SIGTERM` or `SIGINT`, set `stop`, let the current transactions finish, and exit within 20 seconds. Keep `main()` a flat list of tasks so Yifeng can add the health server as one more task.

Add a `worker` service to `compose.yaml`: same image, `command: ["/app/.venv/bin/python", "-m", "app.workers"]`, depends on `migrate` completing. No port and no healthcheck yet; those come with Yifeng's server, at the `TODO(Yifeng)` in `main()`. The loops catch their own errors, so `main()` only waits for the stop signal and then gives the loops `SHUTDOWN_GRACE_SECONDS` to finish. Locally, send `SIGTERM` to the Python process or press Ctrl-C; `uv run` does not forward `SIGTERM`.

### 5.5 Tests

`tests/unit/test_runner.py` with a fake scan: continues after a candidate error; survives a publish failure; backs off after a query error and leaves the gauge unchanged; sets the gauge after an empty pass; stops promptly on `stop`, also between candidates; calls `publish` once per non-`None` result.

`tests/integration/test_worker_scans.py`: each candidate query returns exactly the right sessions; one cycle of each scan clears a prepared backlog; the invariant scan reports a violation inserted through the factories.

`tests/concurrency/test_worker_races.py`, each ending with `capacity_violations(db) == []`:

- Two `expire_session` calls on one session at once: one expiry, one offer.
- Ten `promote_session` calls racing for three seats and eight waiters: offers go to queue numbers 1 to 3, no duplicates.
- A holder keeps the session lock across a deadline while two expiry calls wait: exactly one `EXPIRED` row.
- Expiry racing closure at the close instant: each row ends once, and no offer is created.
- Recovery (FR-07.11): prepare overdue rows, start a fresh runner; the backlog clears and the stored deadlines are unchanged.

Finish Phase 3 by updating TECHNICAL_DESIGN Section 2 (promotion, expiration and the worker loops are implemented).

### 5.6 Cleanup before the first pull request (done)

A review of the whole branch found no correctness problems. Four cleanups were applied: the store function is now `apply_transition` and service locals that hold `ParticipationTransition` lists are named `transitions`; the worker shutdown no longer handles a loop dying, which cannot happen; tests share `tests/helpers.py` (`OFFER_VALIDITY`, `reload`, `session_version`, `latest_history`, `expire`, `promote`), `make_scope` takes `status`, and the runner tests use `publish_nothing`; Jiawei's existing test lines keep their original formatting.

## 6. After my teammates' work lands

**Decline and leave**, once Jiawei merges `core/errors.py`, `services/idempotency.py`, the auth dependency and his first command in `services/participations.py`. Copy his command shell exactly, then add:

- Decline: lock with the caller's account shared, then `expire_if_overdue`. If the offer just expired, commit that and return `409 PARTICIPATION_EXPIRED`, as confirmation does. Otherwise require `OFFERED`, ownership and `now < starts_at`; transition to `CANCELLED` with `OFFER_DECLINED` and the attendee as actor; then `promote_waiters`. Cause `attendee_release`.
- Leave: `end_waiting_if_closed` first. If it ended the entry, commit and return `409 REGISTRATION_CLOSED`. Otherwise require `WAITLISTED`, ownership and `now < starts_at`; transition to `CANCELLED` with `WITHDRAWN`. No promotion. Cause `attendee_release`.
- A repeated command on an attempt already ended by the same action returns `200` with the current result, through the idempotency receipt or the state check.

Review that Jiawei's confirm, cancel, admin cancel and capacity increase call my steps at the sites listed in TECHNICAL_DESIGN 7.3 and 7.4. When `domain/timing.py` lands, replace my two private helpers.

**Publisher**, once Yifeng merges it: pass it to the loops instead of the no-op, and check one expiry, one closure and one promotion against the publication matrix.

**Deployment**, with Yifeng: the worker Deployment uses his probes on `WORKER_METRICS_PORT`; the alerts cover `capacity_violations`, the oldest overdue item and scan freshness.

**Joint validation with Jiawei**: the PRD round of 1,000 mixed requests on a capacity-20 session, confirmation racing expiry, withdraw and rejoin at the tail, and an old cancellation replayed after a new booking. For evaluation: promotion latency P95 at most five seconds over at least 100 observations per cause, the backlog cleared within 30 seconds of a restart, two worker replicas running together, and a demonstration with short validity periods set through the environment.

## 7. Details that are easy to miss

- Recheck registration with the fresh time after every queue-head account lock. An offer written at or after close would break `deadline_at > allocated_at`.
- A seat freed after close stays empty. Closure never promotes, and promotion stops at close.
- Workers write no `request_results` row and create one correlation ID per transaction. They are idempotent by state.
- No `SKIP LOCKED` in the first version. Two workers on one session queue on the lock, and the second finds nothing to do. Add it across sessions only if metrics show contention.
- Expiry inside a promotion-capable transaction keeps the cause `expiration`. Only the promotion scan uses `promotion`.
- Cancellation latency for the report: the offer is written in the cancelling transaction, so compute it from `participation_changes` by correlation ID instead of a live metric.
- `make_waiting` does not advance `last_queue_seq`. Tests that later join through Jiawei's service must set it first.
- A trigger rejection in a test means a service missed a check. Fix the service, not the test.
- Run the tests locally with:

```bash
docker run -d --rm --name booking-test-db -p 55432:5432 -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=booking_test postgres:18
```

```bash
TEST_DATABASE_URL=postgresql+asyncpg://test:test@127.0.0.1:55432/booking_test uv run pytest
```
