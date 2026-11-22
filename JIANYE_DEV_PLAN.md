# Development Plan: Waitlist and Workers (Jianye)

Date: 2026-11-22 · Branch: `jianye/decline-leave` · Based on `main` at `d14b99f`

## Progress log

- **Coordination (2026-11-16 plan).** Recorded service boundaries, lock and transaction rules, and team ownership in the [technical design](doc/TECHNICAL_DESIGN.md), [database design](doc/DATABASE_DESIGN.md), and [project proposal](doc/Project_Proposal.md).
- **2026-11-17 · Shared types and helpers.** Added `SessionChange`, participation transition and lookup helpers, and test factories that create valid history. The suite passed all 41 tests.
- **2026-11-17 · Promotion and expiration.** Implemented FIFO offers, hold and offer expiration, and registration closure. Expiration and replacement offers share one transaction and version bump. The suite passed all 59 tests.
- **2026-11-17 · Workers and validation.** Added the Compose worker, its expiry, closure, promotion and capacity scans, and their metrics. Consolidated test helpers before review. All 75 tests and Ruff passed. Race tests passed five consecutive runs, and a smoke run verified expiry, replacement offers, capacity-violation reporting and graceful shutdown.
- **2026-11-22 · Plan for the remaining work.** Reviewed `main` after Jiawei's booking merge (#10). Decline and leave no longer wait on anyone and need no migration: the state machine, the trigger and the check constraints already allow both transitions. The suite passes 228 tests once the `httpx` import is fixed (Step 1). Jiawei already replaced the private timing helpers in `services/promotion.py` in `cf7693a`, so that item is closed. The team agreed that this branch also fixes the small issues found in their modules.

## Decisions (2026-11-22)

1. **Repeat rule.** A command returns `200` with the unchanged result only when its own end reason is already recorded: `USER_CANCELLED` for cancel, `OFFER_DECLINED` for decline, `WITHDRAWN` for leave, `ADMIN_CANCELLED` for administrator cancel. An attempt that ended any other way returns the `409` that `terminal_code` gives. Cancel and administrator cancel move to this rule too; today they return `200` for any `CANCELLED` attempt.
2. **No conversion between commands.** Leave on an `OFFERED` attempt, or decline on a `HELD` one, returns `409 INVALID_STATE_TRANSITION` and changes nothing. The client refetches and shows the right action.
3. **Tests import `httpx2`**, the package `uv.lock` pins.
4. **One branch, one PR**, in the step order below. Every step ends with a green suite, so the work can pause after any step.
5. **Scope.** This branch also writes the worker health and metrics server and the Compose and `.env.example` settings. The publisher, the gateway, shared logging, Kubernetes and monitoring stay with Yifeng.
6. **Joint validation runs now as in-process tests.** The HTTP load test waits for the deployment.
7. **Promotion latency evidence comes from an offline report** over `participation_changes`. The API gets no live latency metric.
8. **Documents are updated in this branch**, including a full refresh of TECHNICAL_DESIGN Section 2.

## Step overview

| Step | Work | Main files |
| --- | --- | --- |
| 1 | Test and lint baseline | `tests/integration/conftest.py`, `tests/conftest.py`, `pyproject.toml`, `.python-version`, `app/schemas/common.py` |
| 2 | Shared release command, decline and leave | `app/services/participations.py`, `app/api/v1/participations.py` |
| 3 | Tests for decline, leave and the repeat rule | `tests/integration/test_decline_leave.py`, `tests/factories.py`, two existing test files |
| 4 | Races | `tests/concurrency/test_release_races.py`, `tests/helpers.py` |
| 5 | Promotion latency report | `app/cli/promotion_report.py` |
| 6 | Joint validation | `tests/concurrency/test_acceptance.py` |
| 7 | Publication readiness | `app/workers/runner.py`, `tests/integration/test_session_changes.py` |
| 8 | Worker health and metrics server | `app/workers/health.py`, `app/workers/__main__.py` |
| 9 | API readiness, Compose and environment | `app/api/health.py`, `compose.yaml`, `.env.example` |
| 10 | Documents | `doc/*`, this file |
| 11 | Final verification and demo | none |

Suggested commits, one per step: `test: run on a clean checkout`, `feat: decline and leave`, `test: decline and leave`, `test: release races`, `feat: promotion latency report`, `test: acceptance rounds`, `refactor: publish a list of changes`, `feat: worker health server`, `fix: readiness and compose settings`, `docs: sync with decline, leave and workers`.

Run the database suite as before:

```bash
docker run -d --rm --name booking-test-db -p 55432:5432 -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=booking_test postgres:18
TEST_DATABASE_URL=postgresql+asyncpg://test:test@127.0.0.1:55432/booking_test uv run pytest
```

## Step 1: Test and lint baseline

After a clean `uv sync`, `tests/integration` fails to collect (`No module named 'httpx'`) and Ruff reports 77 errors, so no later step could prove itself.

- `tests/integration/conftest.py`: `from httpx2 import ASGITransport, AsyncClient`. Starlette's `TestClient` already imports `httpx2`, so `tests/unit` needs nothing.
- `tests/conftest.py`: build every `Settings` with `_env_file=None`, in both the `settings` and the `engine` fixtures. Otherwise a developer who puts demo values such as `OFFER_VALIDITY_SECONDS=20` in `.env` (Step 11) silently changes the deadlines the tests assert.
- `pyproject.toml`: the 67 B008 reports are FastAPI's `Depends(...)` defaults, and the 8 C408 reports are the deliberate `dict(...)` rows of the authorization matrix.

  ```toml
  [tool.ruff.lint.flake8-bugbear]
  extend-immutable-calls = ["fastapi.Depends", "fastapi.Query", "fastapi.Header", "fastapi.Path", "fastapi.Body"]

  [tool.ruff.lint.per-file-ignores]
  "tests/**" = ["C408"]
  ```

- `app/schemas/common.py`: remove the unused `uuid` import. In `decode_cursor`, decode inside the `try`, then test `isinstance(value, dict)` after it and raise the `AppError` directly (TRY004).
- `.python-version`: `3.14`, matching the Dockerfile and the design. The local `.venv` is Homebrew's 3.15.2 today. `uv` downloads 3.14 and rebuilds the environment on the next run.

Done when: in a fresh clone, `uv sync` followed by the full suite passes on Python 3.14 with no alias, and `uv run ruff check .` reports nothing.

## Step 2: Decline and leave on a shared release command

Cancel, decline and leave differ only in the states they accept, the end reason they write, and whether they free a seat. One private function implements all three, so the check order and the repeat rule cannot drift apart.

```python
@dataclass(frozen=True, slots=True)
class _Release:
    operation: str                # receipt operation; "CANCEL" must not change
    from_states: tuple[S, ...]
    end_reason: EndReason
    frees_seat: bool

_CANCEL = _Release("CANCEL", (S.HELD, S.CONFIRMED), EndReason.USER_CANCELLED, True)
_DECLINE = _Release("DECLINE", (S.OFFERED,), EndReason.OFFER_DECLINED, True)
_LEAVE = _Release("LEAVE", (S.WAITLISTED,), EndReason.WITHDRAWN, False)
```

`cancel`, `decline` and `leave` keep the signature `(factory, settings, *, account, participation_id, request_id) -> CommandOutcome` and call `_release(rule, ...)`. The parameter hash stays `parameter_hash(rule.operation, participation_id=...)`, so cancel receipts already stored still replay. The cause is always `ChangeCause.ATTENDEE_RELEASE`, and history rows use the request ID as correlation ID, as cancel does today.

Check order inside `_release`, the same for all three commands:

| # | Condition | Result | What commits |
| --- | --- | --- | --- |
| 1 | Not the caller's attempt | `404 RESOURCE_NOT_FOUND` | Nothing; the key stays free |
| 2 | The same key committed while waiting for the lock | Stored outcome | Nothing |
| 3 | `_settle` expired an overdue hold or offer | `409 PARTICIPATION_EXPIRED`; the seat is offered | Expiry, offer, receipt, version +1 |
| 4 | `_settle` ended a waiter after registration close | `409 REGISTRATION_CLOSED` | Closure, receipt, version +1 |
| 5 | `CANCELLED` with `rule.end_reason` | `200 CANCELLED`, unchanged | Receipt only |
| 6 | Ended any other way | `409` from `terminal_code` | Receipt only |
| 7 | `now >= starts_at` | `409 SESSION_STARTED` | Receipt only |
| 8 | State not in `rule.from_states` | `409 INVALID_STATE_TRANSITION` | Receipt only |
| 9 | Otherwise | `CANCELLED` with `rule.end_reason`, actor = caller; `_promote` if `frees_seat`; `200 CANCELLED` | Transition, offers, receipt, version +1 |

- Rows 3 and 4 reuse `_settle` and `_reject` unchanged.
- Row 7 is reachable only by cancelling a `CONFIRMED` booking after the start. The row check `registration_closes_at <= starts_at` means that, once a session has started, an offer or hold has already expired (row 3) and a waiter has already been closed (row 4).
- In row 9, an offer made by decline records a null actor. Leave never promotes, even when a seat is free; the promotion scan covers that case.
- `admin_cancel` stays its own function. Its `if p.state == S.CANCELLED` branch becomes rows 5 and 6 with `ADMIN_CANCELLED`. It keeps no start-time check.
- Update the module docstrings of both files, which still say decline and leave are to be added.

Routes in `app/api/v1/participations.py`, copies of the cancel route:

- `POST /participations/{participation_id}/decline` calls `svc.decline`.
- `POST /participations/{participation_id}/leave` calls `svc.leave`.

Both depend on `limit_registration_writes` (they count toward the 30 writes per minute, FR-10.1) and `idempotency_key`, take `body: EmptyBody | None = None`, and return through `_respond`.

Behavior change for Jiawei: cancel on an attempt ended by session or event cancellation now returns `409 SESSION_CANCELLED` or `409 EVENT_CANCELLED` instead of `200`; on a withdrawn or declined attempt it returns `409 INVALID_STATE_TRANSITION`. No existing test relies on the old behavior.

Done when: the existing suite is green, and the generated OpenAPI shows both routes with the `Idempotency-Key` header.

## Step 3: Tests for decline, leave and the repeat rule

Add a `"started"` window to `_WINDOWS` in `tests/factories.py`: `(-3, -2, -1, 1)` hours.

New file `tests/integration/test_decline_leave.py`, with `pytestmark = pytest.mark.usefixtures("capacity_invariant")`. Service-level tests build exact states with the factories. When a test mixes factory waiters with a service-level join, first set `session.last_queue_seq` to the highest seeded number (TECHNICAL_DESIGN 15).

Decline:

- Decline an offer. The attempt ends `OFFER_DECLINED` with the attendee as actor. The next waiter becomes `OFFERED` with a null actor, a deadline of `min(now + 600 s, close)` and the same correlation ID. The waiter behind it moves to position 1. The version rises by exactly one. The `SessionChange` has cause `attendee_release` and transitions `[cancelled, offer_created]`.
- With no waiter, the seat stays free and the transitions are `[cancelled]`.
- An overdue offer returns `409 PARTICIPATION_EXPIRED`. The attempt is `EXPIRED` with `OFFER_EXPIRED` and a null actor, the next waiter is offered, the receipt stores the 409, and the version rises by one.
- An overdue offer in a closed window returns `409 PARTICIPATION_EXPIRED` and creates no offer. In the started window, the result is the same.
- A repeat with a new key returns `200 CANCELLED`, the same version and `changes == []`. The same key returns `replayed` with an identical payload.
- Wrong state, parametrized over `HELD`, `WAITLISTED` and `CONFIRMED`: `409 INVALID_STATE_TRANSITION`, and the row and version are unchanged.
- Ended another way, parametrized over the real services: session cancelled gives `SESSION_CANCELLED`, event cancelled gives `EVENT_CANCELLED`, administrator cancel gives `INVALID_STATE_TRANSITION`.
- Someone else's offer raises a 404 `AppError`, and no receipt row exists.
- A declined attendee can rejoin (FR-06.9): `join_waitlist` creates a new attempt at the tail.

Leave:

- Leave ends the entry `WITHDRAWN` with the attendee as actor. Seed capacity 2 with one hold and two waiters, so a seat is free: still no offer. Later positions move up, the version rises by one, and the transitions are `[cancelled]`.
- Leave and rejoin (FR-06.8): the new attempt gets `queue_seq = last + 1` and the last position. The old attempt stays `CANCELLED`/`WITHDRAWN` and reads as historical.
- After close, and in the started window: `409 REGISTRATION_CLOSED`. The entry is `CANCELLED` with `REGISTRATION_CLOSED` and a null actor, the version rises by one, and the receipt is stored.
- A repeat with a new key returns `200` with the same version; the same key replays.
- Promoted meanwhile: leave on `OFFERED` returns `409 INVALID_STATE_TRANSITION`, and the offer keeps its state and deadline. `HELD` and `CONFIRMED` give the same 409.
- After session cancellation: `409 SESSION_CANCELLED`. Someone else's entry: 404.

Cancel and administrator cancel under the new rule:

- Cancel after session cancellation: `409 SESSION_CANCELLED`. Cancel of a withdrawn entry: `409 INVALID_STATE_TRANSITION`. Cancel of a confirmed booking in the started window: `409 SESSION_STARTED` (FR-08.2). A repeated cancel still returns 200 (existing test).
- Administrator cancel twice: 200, then 200 unchanged, with one audit row. Administrator cancel of a `USER_CANCELLED` attempt: `409 INVALID_STATE_TRANSITION`.

Over HTTP, in the same file with the `client` and `make_user` fixtures:

- Full path: hold, waiter, cancel, then the waiter declines. The response is 200 with `meta.result == "CANCELLED"`, `data.end_reason == "OFFER_DECLINED"` and `meta.session_version == data.version`. The next waiter is `OFFERED`.
- Leave, then `POST /waitlist` again returns 201 at the tail.
- A missing key gives 422, an anonymous call 401, and an unknown body field 422.
- `GET /requests/{key}` returns the stored decline and leave outcomes.

Existing files:

- `tests/integration/test_authz_matrix.py`: add "decline someone else's" and "leave someone else's" with a key. Expect anonymous 401, `a1` 409 (the owner, but the attempt is `HELD`), and 404 for everyone else.
- `tests/contract/test_openapi.py`: add decline, leave and administrator cancel to `COMMANDS`.

Done when: every case above passes, and every row of the Step 2 table is exercised by at least one test.

## Step 4: Races

Move the FIFO audit query from Step 6 into `tests/helpers.py` as `fifo_violations(session_factory)` first, so this step can use it.

New file `tests/concurrency/test_release_races.py`, over independent connections, with the invariant fixture. Repeat the timing-sensitive races ten times, as `test_confirm_vs_expire_single_outcome` does.

- Decline against the expiry worker at the deadline. Create the offer with `minutes=1/60` and sleep 0.98 s. Either decline returns 200 and the worker is a no-op, or decline returns `409 PARTICIPATION_EXPIRED`. In both cases the next waiter receives exactly one offer.
- Decline against confirm from two tabs: exactly one wins, the other gets `409 INVALID_STATE_TRANSITION`, and occupancy is one.
- The queue head leaves while the holder cancels. Either the head is `WITHDRAWN` and the second waiter gets the offer, or the head is `OFFERED` and leave returns 409. Never both, and exactly one offer exists.
- Five concurrent declines with ten waiters behind: the five new offers go to queue numbers 6 to 10, without duplicates.
- Twenty waiters leave while twenty new attendees join: queue numbers stay unique, positions run 1 to N without gaps, and every newcomer is behind every remaining original waiter.
- Five concurrent decline retries with one key: one execution and four replays.
- After each test, `fifo_violations` returns nothing.

Done when: the file passes five consecutive runs.

## Step 5: Promotion latency report

The PRD asks for at least 100 observations per category and P95 of at most 5 s, measured "from the hold deadline or successful cancellation commit to persistence of a waitlist offer". Today only the expiry scan records a live histogram, and nothing reads cancellations.

New module `app/cli/promotion_report.py`, run as `python -m app.cli.promotion_report [--since ISO-8601] [--session UUID]`. It only reads, and it runs anywhere the image can reach the database.

- `async def promotion_latencies(db, *, since=None, session_id=None) -> LatencyReport` returns the seconds per category and the unpaired counts.
- A release is a history row from `HELD`, `OFFERED` or `CONFIRMED` to `CANCELLED` or `EXPIRED`. With `HOLD_EXPIRED` or `OFFER_EXPIRED` it is category `expiration`, freed at the attempt's `deadline_at`. With `USER_CANCELLED`, `OFFER_DECLINED` or `ADMIN_CANCELLED` it is category `cancellation`, freed at the row's `occurred_at`. Other reasons free no seat that promotion may use.
- An offer is a history row to `OFFERED`.
- Within one correlation ID, releases ordered by freed time are paired one to one with offers ordered by queue number. This is the rule `expire_session` already uses for the live metric. Offers without a release (a capacity increase, the promotion scan) and releases without an offer (registration closed, no waiter, or a seat beyond the 20-offer limit) are counted and excluded.
- Latency is the offer's `occurred_at` minus the freed time. For each category, report n, P50, P95 (nearest rank) and maximum, plus "target met" when n is at least 100 and P95 is at most 5 s.
- `main()` builds the engine from `Settings`, prints one line per category and the unpaired counts, and exits 0.

Test `tests/integration/test_promotion_report.py`: seed one expiry through `helpers.expire`, one cancel and one decline with waiters, and one capacity increase. Assert the categories, the counts, the non-negative values, the unpaired counts and the session filter.

Done when: the test passes and the command prints a report against the Compose database.

## Step 6: Joint validation

New file `tests/concurrency/test_acceptance.py`. Jiawei reviews it; nothing in it waits for him.

**PRD round (capacity and duplicate bookings).**

- Setup: capacity 20, 100 accounts acting as 100 concurrent clients, 1,000 write commands per round, and hold and offer validity of 2 s. An expiry loop and a promotion loop run at 0.2 s during the round.
- Each client reads its own participation, then picks the next command from its state:
  - None: book, or join after `WAITLIST_REQUIRED`.
  - `HELD`: confirm success, confirm failure, cancel, or let it expire.
  - `OFFERED`: confirm, decline, or let it expire.
  - `WAITLISTED`: leave, join again (a duplicate), or wait.
  - `CONFIRMED`: cancel, or confirm again (a duplicate).
  - About 5% of commands resend the previous key.
- Use `random.Random(seed)` and print the seed, so a failure can be replayed.
- A watcher task runs the capacity-invariant query every 50 ms during the round.
- After the round, stop the loops and run expiry and promotion once more. Then assert:
  - The watcher never saw a violation.
  - No account has two active attempts.
  - There was no unexpected exception. `LOCK_TIMEOUT` is counted, not failed.
  - Every resent key returned its original outcome.
  - `fifo_violations` is empty.
  - The promotion scan finds no open session with both a free seat and a waiter.
- Rounds come from the `ACCEPTANCE_ROUNDS` environment variable: 1 by default, 10 for the recorded evidence run. Print a summary per round: outcome counts, busy count and duration.

FIFO audit: an offer made while an older waiter was still waiting.

```sql
SELECT o.participation_id
FROM participation_changes o
JOIN participations p ON p.id = o.participation_id
JOIN participations w ON w.session_id = p.session_id AND w.queue_seq < p.queue_seq
JOIN participation_changes wj ON wj.participation_id = w.id AND wj.to_state = 'WAITLISTED'
LEFT JOIN participation_changes wl ON wl.participation_id = w.id AND wl.from_state = 'WAITLISTED'
WHERE o.to_state = 'OFFERED'
  AND wj.occurred_at < o.occurred_at
  AND (wl.occurred_at IS NULL OR wl.occurred_at > o.occurred_at);
```

**Other scenarios from the PRD and the old plan.**

- Waitlist fairness. Add the two cases not yet covered: the queue head withdraws, and the next waiter receives the freed seat; an offer expires, the seat passes to the next waiter, and the expired attendee does not rejoin the queue (FR-07.10). Single seat, several seats and multiple workers already exist in `test_promotion.py` and `test_worker_races.py`.
- Old cancellation replayed after a new booking. Book (K1), cancel (K2), book again (K3). Resending K2 returns the stored 200 for the old attempt, and the new hold, the version and the occupancy are unchanged. A new key on the old attempt returns 200 unchanged under the repeat rule.
- Two worker replicas. Two full sets of the four scans run together over 50 sessions that have overdue holds and waiters. Every overdue row expires exactly once (count the `EXPIRED` history rows), every freed seat produces one offer, and `fifo_violations` is empty.
- Promotion latency evidence. One session holds 120 holds whose deadlines are spread over 8 s, about 15 per scan pass, which stays under the 20-offer limit. It also has 240 waiters. The expiry loop runs at the default 1 s. After that, release 120 of the resulting offers by declining some and confirming then cancelling the rest. `promotion_latencies` must show at least 100 observations in both categories, with P95 at most 5 s.
- Already covered, and only cited in the report: confirmation racing expiry (`test_booking_races.py`) and the backlog cleared after a restart (`test_worker_races.py`).

Done when: the file passes in the normal suite, and one run with `ACCEPTANCE_ROUNDS=10` is recorded in the progress log with the machine details and the latency figures.

## Step 7: Publication readiness without the publisher

- Fix the publisher entry point in TECHNICAL_DESIGN 10.3: `async def publish(redis: Redis | None, changes: Sequence[SessionChange]) -> None`, which never raises. The API's `publish_after_commit` already matches it.
- `app/workers/runner.py`: `Publish = Callable[[Sequence[SessionChange]], Awaitable[None]]`, and `_process` calls `publish([change])`. Update `publish_nothing` and `tests/unit/test_runner.py`. When the publisher lands, the worker needs one line in `__main__`: `publish=functools.partial(publisher.publish, redis)`.
- New file `tests/integration/test_session_changes.py`, one table-driven test over every row of the TECHNICAL_DESIGN 10.5 matrix that produces a `SessionChange`. Each case runs the real operation and asserts `cause` and the transition reasons:
  - Hold, join and confirmation.
  - Cancel, decline and leave: `attendee_release` with `cancelled`, plus `offer_created` when a waiter is promoted.
  - Administrator cancel.
  - Expiration: `expired` plus `offer_created`.
  - Closure: `registration_closed`, with `expired` for holds and offers and `cancelled` for waiters.
  - Promotion scan: `promotion` with `offer_created`.
  - Capacity increase: `capacity_changed` with `offer_created`.
  - Session publication, session cancellation, and event cancellation (one change per session).
  - A simulated payment failure and a stale worker candidate return no change.

This replaces the old item "check one expiry, one closure and one promotion against the publication matrix", done ahead of the publisher.

Done when: the test passes and `tests/unit/test_runner.py` is green.

## Step 8: Worker health and metrics server

TECHNICAL_DESIGN 13.3 fixes the contract, and the TODO sits in `app/workers/__main__.py`. Yifeng keeps the probes, the scrape configuration and the alerts.

New module `app/workers/health.py`:

- `build_app(engine) -> Starlette` with three routes:
  - `/health/live`: 200, touches nothing.
  - `/health/ready`: 200 when `SELECT 1` answers within 2 s, otherwise 503. It never checks Redis or scan age.
  - `/metrics`: `generate_latest()` with `CONTENT_TYPE_LATEST`.
- `async def serve(engine, port, stop) -> None` runs uvicorn with `access_log=False` and `lifespan="off"`. Subclass `uvicorn.Server` and override `capture_signals` with an empty context manager. uvicorn 0.54 otherwise replaces the process's SIGTERM and SIGINT handlers, which would break the worker's graceful shutdown.

`app/workers/__main__.py`:

- Start `serve` as one more task beside the scan loops. On stop, set `server.should_exit`.
- If the server task ends before stop, for example because the port is taken, log it, set stop and exit with status 1, so Kubernetes restarts the Pod. uvicorn reports a failed bind with `sys.exit(3)`, so the task wrapper must catch `SystemExit` as well as `Exception`.
- Remove the TODO.

`compose.yaml`: give the worker a healthcheck on `http://127.0.0.1:9100/health/live`, written like the API's.

Tests:

- `tests/integration/test_worker_health.py` drives `build_app` through `httpx2.ASGITransport`. Live returns 200. Ready returns 200 with the test engine and 503 with an engine pointed at a closed port. After one `run_scan_once`, `/metrics` contains `worker_last_scan_timestamp_seconds`.
- `tests/unit/test_worker_health.py`: `serve` on port 0 starts, returns within one second of stop, and leaves the SIGTERM handler as it found it.

Done when: both tests pass, and `docker compose ps` shows the worker healthy.

## Step 9: API readiness, Compose and environment

- `app/api/health.py`: readiness returns 503 only when PostgreSQL fails. A Redis failure, or no Redis client, is reported as `"redis": "degraded: ..."` with status 200, because TECHNICAL_DESIGN 12 and 13.3 keep the API in service during Redis loss.
  - New tests in `tests/integration/test_health_ready.py`: with the database up and Redis set to `None`, the API returns 200 and reports Redis as degraded. With the database unreachable, it returns 503.
  - The integration `app` fixture does not set `app.state.engine`, so the test sets it.
- `compose.yaml`:
  - Give `api` and `worker` `env_file: [{path: .env, required: false}]`, so every variable of TECHNICAL_DESIGN 13.5 placed in `.env` reaches the containers without repeating defaults. Settings ignores unknown keys, and the explicit `DATABASE_URL` in `x-app-env` still wins.
  - The API and the worker then always share `OFFER_VALIDITY_SECONDS`, which matters because both create offers.
- `.env.example`: list every TECHNICAL_DESIGN 13.5 variable with its default, grouped by process. Mark the optional ones as comments.

Done when: the tests pass, and `docker compose config` shows the variables from a sample `.env` on both services.

## Step 10: Documents

- `doc/TECHNICAL_DESIGN.md`, version 1.5. Add a "Changes in 1.5" line, then update:
  - Section 2: rewrite it from the code. Implemented: the database layer; accounts, auth and administration; events and sessions; book, join, confirm, cancel, decline, leave and administrator cancel; idempotency; rate limiting; promotion, expiration and the four scans; the worker health server. Remaining: Idempotency-Key on management commands, the publisher, the gateway and tickets, shared logging, API `/metrics`, the frontend, Kubernetes, monitoring and load tests.
  - Section 5: add `workers/health.py` and `cli/promotion_report.py` to the tree.
  - Section 5.1: add the report command.
  - Section 7.3: add the repeat rule and the no-conversion rule.
  - Section 8.4: "repeated terminal command" now means the command's own end reason is recorded.
  - Section 10.3: the publisher signature.
  - Section 13.1: Compose passes `.env` to the API and the worker.
  - Section 15: no alias is needed, Python 3.14, and `ACCEPTANCE_ROUNDS`.
- `doc/DATABASE_DESIGN.md`, version 1.5: in "Confirm, cancel, decline or leave", step 5 states the repeat rule.
- `doc/openapi.yaml`: the cancel, decline, leave and administrator cancel descriptions state the repeat rule and the 409 codes each can return. They also note that `PARTICIPATION_EXPIRED` and `REGISTRATION_CLOSED` commit the expiry or closure first.
- `doc/Project_Proposal.md`, ownership table: the worker health server code, the promotion report and the acceptance tests become Jianye's; the probes, the scrape configuration and the alerts stay Yifeng's.
- This file: one progress entry per finished step, with test counts and evidence figures.

Done when: every route, variable and module these documents name exists in the code.

## Step 11: Final verification and demo

1. In a fresh clone: `uv sync`, the full suite on 3.14, and `uv run ruff check .`.
2. `tests/concurrency` passes five consecutive runs. `ACCEPTANCE_ROUNDS=10` passes once.
3. Compose smoke test with `HOLD_VALIDITY_SECONDS=20` and `OFFER_VALIDITY_SECONDS=20` in `.env`:
   - An organizer creates a capacity-1 session. A books; B, C and D join.
   - A's hold expires, and B is offered (worker log).
   - B declines, and C is offered.
   - D leaves.
   - C confirms.
   - Check states, positions and versions through `my-participation` and the roster.
   - From inside the container, check the worker's `/health/ready` and `/metrics`.
   - Run `python -m app.cli.promotion_report`.
4. `docker compose stop worker` ends within the 20 s grace period and logs "worker stopped".
5. Remove the demo values from `.env`. Record the results in the progress log.

## Left to teammates

- **Jiawei**: Idempotency-Key on the management commands, which covers 8 routes: create and edit event, publish, cancel event, create and edit session, cancel session, and edit account. It is his open TODO and changes the transaction shape of every management service, so this branch does not touch it. I can take it after this branch merges if he hands it over.
- **Yifeng**: the publisher, the gateway and tickets, `core/logging.py`, API `/metrics`, Kubernetes and monitoring. After the publisher lands, I wire it into the worker (one line) and switch the worker to shared logging.
- **Alert expressions for Yifeng's rules**:
  - Capacity: `max(capacity_violations) > 0`, immediately, critical.
  - Backlog: `max(worker_oldest_overdue_seconds) > 30` for 1 minute.
  - Scan freshness: `time() - worker_last_scan_timestamp_seconds > 30` for 1 minute, plus `absent(worker_last_scan_timestamp_seconds{scan="<each scan>"})`. Both apply only to Ready worker Pods.

## Accepted without change

- A capacity increase does not expire overdue attempts first, so it may offer fewer seats than are about to be free. The expiry scan closes that gap within about a second.
- Promotions inside attendee and administrator commands have no live latency metric. Step 5 covers them offline.
- `SESSION_STARTED` stays as a guard for decline and leave even though valid data cannot reach it (Step 2, row 7).

## Definition of done

- Every step's "Done when" holds, and the suite and Ruff are green from a fresh clone on Python 3.14.
- Five consecutive concurrency runs and one 10-round acceptance run are recorded.
- Both promotion categories have at least 100 observations with P95 at most 5 s locally.
- The documents match the code. Jiawei has reviewed Steps 2, 3 and 6, and Yifeng has reviewed Steps 7 to 9.

## Messages to send when the PR opens

- **Jiawei**: the shared release command and the cancel behavior change, the `httpx2` import, the Ruff configuration, and a review of the acceptance tests.
- **Yifeng**: the worker health server, the publisher signature, Compose `env_file`, and the alert expressions.
- **Everyone**: run `uv sync` after the merge, because the virtual environment is rebuilt for Python 3.14. Demo values in `.env` no longer affect the tests.
