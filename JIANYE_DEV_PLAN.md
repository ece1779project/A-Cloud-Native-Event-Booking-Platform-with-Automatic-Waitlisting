# Development Plan: Waitlist and Workers (Jianye)

Date: 2026-11-22 · Branch: `jianye/decline-leave` · Based on `main` at `d14b99f`

## Progress log

- **Coordination (2026-11-16 plan).** Recorded service boundaries, lock and transaction rules, and team ownership in the [technical design](doc/TECHNICAL_DESIGN.md), [database design](doc/DATABASE_DESIGN.md), and [project proposal](doc/Project_Proposal.md).
- **2026-11-17 · Shared types and helpers.** Added `SessionChange`, participation transition and lookup helpers, and test factories that create valid history. The suite passed all 41 tests.
- **2026-11-17 · Promotion and expiration.** Implemented FIFO offers, hold and offer expiration, and registration closure. Expiration and replacement offers share one transaction and version bump. The suite passed all 59 tests.
- **2026-11-17 · Workers and validation.** Added the Compose worker, its expiry, closure, promotion and capacity scans, and their metrics. Consolidated test helpers before review. All 75 tests and Ruff passed. Race tests passed five consecutive runs, and a smoke run verified expiry, replacement offers, capacity-violation reporting and graceful shutdown.
- **2026-11-22 · Plan for the remaining work.** Reviewed `main` after Jiawei's booking merge (#10). Decline and leave no longer wait on anyone and need no migration: the state machine, the trigger and the check constraints already allow both transitions. The suite passes 228 tests once the `httpx` import is fixed (Step 1). Jiawei already replaced the private timing helpers in `services/promotion.py` in `cf7693a`, so that item is closed. This plan covers only my own work. It touches teammates' files only where that work needs it, which they agreed to.
- **2026-11-22 · Steps 1 to 3.** The suite runs from a clean `uv sync` on Python 3.14 and Ruff is clean. Cancel, decline and leave share `_release`; cancel and administrator cancel follow the repeat rule. Added 45 tests (`test_decline_leave.py`, authorization matrix and contract rows). All 273 tests pass.
- **2026-11-22 · Steps 4 to 6.** Added `fifo_violations`, six release races, `python -m app.cli.promotion_report` and the acceptance tests. All 288 tests and Ruff pass. Evidence on an Apple M4 Pro (14 cores, 24 GB) with PostgreSQL 18.6 in Docker: ten capacity rounds (capacity 20, 100 clients, 1,000 commands each) passed with no capacity violation, duplicate active attempt or FIFO violation; promotion latency was n=120, P95 1.02 s, max 1.10 s for expiration and n=120, P95 0.004 s for cancellation. Of 24 runs of `tests/concurrency`, one failed once and could not be reproduced in the next 18; its output was not kept, so the test is unknown.
- **2026-11-22 · Steps 7 to 9.** `test_session_changes.py` checks 19 cases against the publication matrix: cause, transition reasons, and one version bump per changed session. Compose passes `.env` to the API and the worker; with a sample `.env`, `docker compose config` shows both validity values on both services, and the assembled `DATABASE_URL` still wins. The technical design (1.5), the database design (1.6) and the OpenAPI descriptions state the repeat and no-conversion rules. All 307 tests and Ruff pass on Python 3.14.

## Decisions (2026-11-22)

1. **Repeat rule.** A command returns `200` with the unchanged result only when its own end reason is already recorded: `USER_CANCELLED` for cancel, `OFFER_DECLINED` for decline, `WITHDRAWN` for leave, `ADMIN_CANCELLED` for administrator cancel. An attempt that ended any other way returns the `409` that `terminal_code` gives. Cancel and administrator cancel move to this rule too, because decline and leave share their code (Step 2). Today they return `200` for any `CANCELLED` attempt.
2. **No conversion between commands.** Leave on an `OFFERED` attempt, or decline on a `HELD` one, returns `409 INVALID_STATE_TRANSITION` and changes nothing. The client refetches and shows the right action.
3. **Tests import `httpx2`**, the package `uv.lock` pins.
4. **One branch, one PR**, in the step order below. Every step ends with a green suite, so the work can pause after any step.
5. **Scope.** Only work that is mine and needs nothing from teammates. Work assigned to others in the ownership table of the project proposal is not in this plan, even when it is still open.
6. **Joint validation runs now as in-process tests.** The HTTP load test waits for the deployment.
7. **Promotion latency evidence comes from an offline report** over `participation_changes`. No live latency metric is added.
8. **Documents change only where this branch changes behavior or adds a module.**

## Step overview

| Step | Work | Main files |
| --- | --- | --- |
| 1 | Test and lint baseline (done) | `tests/integration/conftest.py`, `tests/conftest.py`, `pyproject.toml`, `.python-version`, `app/schemas/common.py` |
| 2 | Shared release command, decline and leave (done) | `app/services/participations.py`, `app/api/v1/participations.py` |
| 3 | Tests for decline, leave and the repeat rule (done) | `tests/integration/test_decline_leave.py`, `tests/factories.py`, two existing test files |
| 4 | Races (done) | `tests/concurrency/test_release_races.py`, `tests/helpers.py` |
| 5 | Promotion latency report (done) | `app/cli/promotion_report.py` |
| 6 | Joint validation (done) | `tests/concurrency/test_acceptance.py` |
| 7 | Publication matrix check on `SessionChange` (done) | `tests/integration/test_session_changes.py` |
| 8 | Validity settings for the demo (done) | `compose.yaml`, `.env.example` |
| 9 | Documents (done) | `doc/TECHNICAL_DESIGN.md`, `doc/DATABASE_DESIGN.md`, `doc/openapi.yaml`, this file |
| 10 | Final verification and demo | none |

Suggested commits, one per step: `test: run on a clean checkout`, `feat: decline and leave`, `test: decline and leave`, `test: release races`, `feat: promotion latency report`, `test: acceptance rounds`, `test: session changes follow the publication matrix`, `chore: pass validity settings to compose`, `docs: decline, leave and the repeat rule`.

Run the database suite as before:

```bash
docker run -d --rm --name booking-test-db -p 55432:5432 -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=booking_test postgres:18
TEST_DATABASE_URL=postgresql+asyncpg://test:test@127.0.0.1:55432/booking_test uv run pytest
```

## Step 1: Test and lint baseline (done 2026-11-22)

After a clean `uv sync`, `tests/integration` fails to collect (`No module named 'httpx'`), and Ruff reports 77 errors. Until both are fixed, none of my later tests can run on a clean checkout, and lint cannot gate the PR.

- `tests/integration/conftest.py`: `from httpx2 import ASGITransport, AsyncClient`. Starlette's `TestClient` already imports `httpx2`, so `tests/unit` needs nothing.
- `tests/conftest.py`: build every `Settings` with `_env_file=None`, in both the `settings` and the `engine` fixtures. Without this, demo values such as `OFFER_VALIDITY_SECONDS=20` in `.env` (Step 10) silently change the deadlines the tests assert.
- `pyproject.toml`: 67 of the reports are B008 on FastAPI's `Depends(...)` defaults. Another 8 are C408 on the authorization matrix's `dict(...)` rows, which are written that way on purpose. I verified that this configuration leaves only the two errors fixed in the next item:

  ```toml
  [tool.ruff.lint.flake8-bugbear]
  extend-immutable-calls = ["fastapi.Depends", "fastapi.Query", "fastapi.Header", "fastapi.Path", "fastapi.Body"]

  [tool.ruff.lint.per-file-ignores]
  "tests/**" = ["C408"]
  ```

- `app/schemas/common.py`:
  - Remove the unused `uuid` import.
  - In `decode_cursor`, keep the decoding inside the `try`. Move the `isinstance(value, dict)` test after it, and raise the `AppError` directly (TRY004).
- `.python-version`: `3.14`, matching the Dockerfile and the design. The local `.venv` currently runs Homebrew's Python 3.15.2. `uv` downloads 3.14 and rebuilds the environment on the next run.

Done when: in a fresh clone, `uv sync` followed by the full suite passes on Python 3.14 with no alias, and `uv run ruff check .` reports nothing.

## Step 2: Decline and leave on a shared release command (done 2026-11-22)

Cancel, decline and leave differ only in three ways: the states they accept, the end reason they write, and whether they free a seat. One private function implements all three, so the check order and the repeat rule cannot drift apart.

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

`cancel`, `decline` and `leave` keep the signature `(factory, settings, *, account, participation_id, request_id) -> CommandOutcome`, and each calls `_release(rule, ...)`.

- The parameter hash stays `parameter_hash(rule.operation, participation_id=...)`, so cancel receipts that are already stored still replay.
- The cause is always `ChangeCause.ATTENDEE_RELEASE`.
- History rows use the request ID as the correlation ID, as cancel does today.

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
- With valid data, row 7 is reachable only by cancelling a `CONFIRMED` booking after the session starts. The row check `registration_closes_at <= starts_at` means that by the start time, any offer or hold has already expired (row 3) and any waiter has already been closed (row 4).
- In row 9, an offer created by a decline records a null actor. Leave never promotes, even when a seat is free; the promotion scan covers that case.
- `admin_cancel` stays its own function. Its `if p.state == S.CANCELLED` branch becomes rows 5 and 6, using `ADMIN_CANCELLED`. It still has no start-time check.
- Update the module docstrings of both files, which still say decline and leave are to be added.

Routes in `app/api/v1/participations.py`, both copies of the cancel route:

- `POST /participations/{participation_id}/decline` calls `svc.decline`.
- `POST /participations/{participation_id}/leave` calls `svc.leave`.

Both depend on `limit_registration_writes`, so they count toward the 30 writes per minute (FR-10.1). Both also depend on `idempotency_key`, take `body: EmptyBody | None = None`, and return through `_respond`.

Behavior change to tell Jiawei about: cancel on an attempt ended by session or event cancellation now returns `409 SESSION_CANCELLED` or `409 EVENT_CANCELLED` instead of `200`. On a withdrawn or declined attempt, it returns `409 INVALID_STATE_TRANSITION`. No existing test relies on the old behavior.

Done when: the existing suite is green, and the generated OpenAPI shows both routes with the `Idempotency-Key` header.

## Step 3: Tests for decline, leave and the repeat rule (done 2026-11-22)

Add a `"started"` window to `_WINDOWS` in `tests/factories.py`: `(-3, -2, -1, 1)` hours.

New file `tests/integration/test_decline_leave.py`, with `pytestmark = pytest.mark.usefixtures("capacity_invariant")`. Service-level tests build exact states with the factories. When a test mixes factory waiters with a service-level join, first set `session.last_queue_seq` to the highest seeded number (TECHNICAL_DESIGN 15).

Decline:

- Decline an offer:
  - The attempt ends `OFFER_DECLINED`, with the attendee as actor.
  - The next waiter becomes `OFFERED` with a null actor, a deadline of `min(now + 600 s, close)`, and the same correlation ID.
  - The waiter behind it moves to position 1.
  - The version rises by exactly one.
  - The `SessionChange` has cause `attendee_release` and transitions `[cancelled, offer_created]`.
- With no waiter, the seat stays free and the transitions are `[cancelled]`.
- An overdue offer:
  - Returns `409 PARTICIPATION_EXPIRED`.
  - The attempt is `EXPIRED` with `OFFER_EXPIRED` and a null actor, and the next waiter is offered.
  - The receipt stores the 409, and the version rises by one.
- An overdue offer in a closed window returns `409 PARTICIPATION_EXPIRED` and creates no offer. The started window gives the same result.
- A repeat with a new key returns `200 CANCELLED`, the same version, and `changes == []`. The same key returns `replayed` with an identical payload.
- Wrong state, parametrized over `HELD`, `WAITLISTED` and `CONFIRMED`: `409 INVALID_STATE_TRANSITION`, and the row and version are unchanged.
- An offer that was ended another way, parametrized over the real services:
  - Session cancelled: `SESSION_CANCELLED`.
  - Event cancelled: `EVENT_CANCELLED`.
  - Administrator cancel: `INVALID_STATE_TRANSITION`.
- Someone else's offer raises a 404 `AppError`, and no receipt row exists.
- A declined attendee can rejoin (FR-06.9): `join_waitlist` creates a new attempt at the tail.

Leave:

- A plain leave:
  - The entry ends `WITHDRAWN`, with the attendee as actor.
  - Seed capacity 2 with one hold and two waiters, so that a seat is free. Leave still creates no offer.
  - Later positions move up, the version rises by one, and the transitions are `[cancelled]`.
- Leave and rejoin (FR-06.8): the new attempt gets `queue_seq = last + 1` and the last position. The old attempt stays `CANCELLED`/`WITHDRAWN` and reads as historical.
- After close, and in the started window:
  - Returns `409 REGISTRATION_CLOSED`.
  - The entry is `CANCELLED` with `REGISTRATION_CLOSED` and a null actor.
  - The version rises by one, and the receipt is stored.
- A repeat with a new key returns `200` with the same version; the same key replays.
- Promoted meanwhile: leave on `OFFERED` returns `409 INVALID_STATE_TRANSITION`, and the offer keeps its state and deadline. `HELD` and `CONFIRMED` give the same 409.
- After session cancellation: `409 SESSION_CANCELLED`. Someone else's entry: 404.

Cancel and administrator cancel under the new rule:

- Cancel after session cancellation: `409 SESSION_CANCELLED`.
- Cancel of a withdrawn entry: `409 INVALID_STATE_TRANSITION`.
- Cancel of a confirmed booking in the started window: `409 SESSION_STARTED` (FR-08.2).
- A repeated cancel still returns 200 (existing test).
- Administrator cancel twice: 200, then 200 unchanged, with one audit row.
- Administrator cancel of a `USER_CANCELLED` attempt: `409 INVALID_STATE_TRANSITION`.

Over HTTP, in the same file with the `client` and `make_user` fixtures:

- Full path: hold, waiter, cancel, then the waiter declines.
  - The response is 200, with `meta.result == "CANCELLED"`, `data.end_reason == "OFFER_DECLINED"` and `meta.session_version == data.version`.
  - The next waiter is `OFFERED`.
- Leave, then `POST /waitlist` again returns 201 at the tail.
- A missing key gives 422, an anonymous call 401, and an unknown body field 422.
- `GET /requests/{key}` returns the stored decline and leave outcomes.

Existing files:

- `tests/integration/test_authz_matrix.py`: add "decline someone else's" and "leave someone else's", both sent with a key.
  - Anonymous gets 401.
  - `a1` gets 409: it owns the attempt, but the attempt is `HELD`.
  - Everyone else gets 404.
- `tests/contract/test_openapi.py`: add decline, leave and administrator cancel to `COMMANDS`.

Done when: every case above passes, and at least one test exercises each row of the Step 2 table.

## Step 4: Races (done 2026-11-22)

First move the FIFO audit query from Step 6 into `tests/helpers.py` as `fifo_violations(session_factory)`, so this step can use it.

New file `tests/concurrency/test_release_races.py`, over independent connections, with the invariant fixture. Repeat the timing-sensitive races ten times, as `test_confirm_vs_expire_single_outcome` does.

- Decline against the expiry worker at the deadline. Create the offer with `minutes=1/60` and sleep 0.98 s. One of two outcomes must happen:
  - Decline returns 200, and the worker is a no-op.
  - Decline returns `409 PARTICIPATION_EXPIRED`.

  Either way, the next waiter receives exactly one offer.
- Decline against confirm from two tabs: exactly one wins, the other gets `409 INVALID_STATE_TRANSITION`, and occupancy is one.
- The queue head leaves while the holder cancels. One of two outcomes must happen, never both:
  - The head is `WITHDRAWN`, and the second waiter gets the offer.
  - The head is `OFFERED`, and leave returns 409.

  Exactly one offer exists afterwards.
- Five concurrent declines with ten waiters behind: the five new offers go to queue numbers 6 to 10, without duplicates.
- Twenty waiters leave while twenty new attendees join:
  - Queue numbers stay unique.
  - Positions run from 1 to N without gaps.
  - Every newcomer is behind every remaining original waiter.
- Five concurrent decline retries with one key: one execution and four replays.
- After each test, `fifo_violations` returns nothing.

Done when: the file passes five consecutive runs.

## Step 5: Promotion latency report (done 2026-11-22)

The PRD requires at least 100 observations per category, with P95 at most 5 s, measured "from the hold deadline or successful cancellation commit to persistence of a waitlist offer". Today only the expiry scan records a live histogram, and nothing measures cancellations.

New module `app/cli/promotion_report.py`, run as `python -m app.cli.promotion_report [--since ISO-8601] [--session UUID]`. It only reads, and it runs anywhere the image can reach the database.

- `async def promotion_latencies(db, *, since=None, session_id=None) -> LatencyReport` returns the seconds per category and the unpaired counts.
- A release is a history row from `HELD`, `OFFERED` or `CONFIRMED` to `CANCELLED` or `EXPIRED`. Its reason decides the category:
  - `HOLD_EXPIRED` or `OFFER_EXPIRED`: category `expiration`, freed at the attempt's `deadline_at`.
  - `USER_CANCELLED`, `OFFER_DECLINED` or `ADMIN_CANCELLED`: category `cancellation`, freed at the row's `occurred_at`.
  - Other reasons free no seat that promotion can use.
- An offer is a history row to `OFFERED`.
- Pairing: within one correlation ID, sort releases by freed time and offers by queue number, then pair them one to one. `expire_session` already uses this rule for the live metric.
  - Offers without a release are counted and excluded. These come from a capacity increase or the promotion scan.
  - Releases without an offer are counted and excluded. These happen after registration closes, when no one is waiting, or when the seat falls beyond the 20-offer limit.
- Latency is the offer's `occurred_at` minus the freed time. For each category the report gives n, P50, P95 (nearest rank) and the maximum, plus "target met" when n is at least 100 and P95 is at most 5 s.
- `main()` builds the engine from `Settings`, prints one line per category and the unpaired counts, and exits 0.

Test `tests/integration/test_promotion_report.py`:

- Seed one expiry through `helpers.expire`, one cancel and one decline with waiters, and one capacity increase.
- Assert the categories, the counts, that every value is non-negative, the unpaired counts, and the session filter.

Done when: the test passes, and the command prints a report against the Compose database.

## Step 6: Joint validation (done 2026-11-22)

New file `tests/concurrency/test_acceptance.py`. Jiawei reviews it, but nothing in it waits for him.

**PRD round (capacity and duplicate bookings).**

- Setup: capacity 20, 100 accounts acting as 100 concurrent clients, and 1,000 write commands per round. Hold and offer validity are 2 s. An expiry loop and a promotion loop run every 0.2 s during the round.
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
  - No unexpected exception occurred. `LOCK_TIMEOUT` is counted, not treated as a failure.
  - Every resent key returned its original outcome.
  - `fifo_violations` is empty.
  - The promotion scan finds no open session with both a free seat and a waiter.
- The `ACCEPTANCE_ROUNDS` environment variable sets the number of rounds: 1 by default, 10 for the recorded evidence run. Print a summary per round: outcome counts, busy count and duration.

FIFO audit: find any offer made while an older waiter was still waiting.

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

- Waitlist fairness. Add the two cases not yet covered:
  - The queue head withdraws, and the next waiter receives the freed seat.
  - An offer expires, the seat passes to the next waiter, and the expired attendee does not rejoin the queue (FR-07.10).

  `test_promotion.py` and `test_worker_races.py` already cover a single seat, several seats and multiple workers.
- Old cancellation replayed after a new booking. Book with key K1, cancel with K2, then book again with K3.
  - Resending K2 returns the stored 200 for the old attempt.
  - The new hold, the version and the occupancy are unchanged.
  - Under the repeat rule, a new key on the old attempt also returns 200 unchanged.
- Two worker replicas. Two full sets of the four scans run together over 50 sessions that have overdue holds and waiters.
  - Every overdue row expires exactly once; count the `EXPIRED` history rows.
  - Every freed seat produces exactly one offer.
  - `fifo_violations` is empty.
- Promotion latency evidence. Seed one session with 120 holds and 240 waiters.
  - Spread the hold deadlines over 8 s. That gives about 15 expiries per scan pass, under the 20-offer limit.
  - Run the expiry loop at the default 1 s.
  - Then release 120 of the resulting offers: decline some, and confirm then cancel the rest.
  - `promotion_latencies` must show at least 100 observations in both categories, with P95 at most 5 s.
- Already covered, so only cited in the report: confirmation racing expiry (`test_booking_races.py`) and the backlog cleared after a restart (`test_worker_races.py`).

Done when: the file passes in the normal suite, and one run with `ACCEPTANCE_ROUNDS=10` is recorded in the progress log, with the machine details and the latency figures.

## Step 7: Publication matrix check on `SessionChange` (done 2026-11-22)

`SessionChange` and its enums are mine, and they are the publisher's only input. This step checks every value against TECHNICAL_DESIGN 10.5 now, so integrating the publisher later should not require changes on my side.

New file `tests/integration/test_session_changes.py`, one table-driven test over every row of the TECHNICAL_DESIGN 10.5 matrix that produces a `SessionChange`. Each case runs the real operation and asserts `cause` and the transition reasons:

- Hold, join and confirmation.
- Cancel, decline and leave: `attendee_release`, with `cancelled` plus `offer_created` when a waiter is promoted.
- Administrator cancel.
- Expiration: `expired` plus `offer_created`.
- Closure: `registration_closed`, with `expired` for holds and offers and `cancelled` for waiters.
- Promotion scan: `promotion` with `offer_created`.
- Capacity increase: `capacity_changed` with `offer_created`.
- Session publication, session cancellation, and event cancellation (one change per session).
- A simulated payment failure and a stale worker candidate return no change.

Done when: the test passes.

## Step 8: Validity settings for the demo (done 2026-11-22)

The demo needs short validity periods set through the environment, but Compose passes neither variable today. The API and the worker must also share `OFFER_VALIDITY_SECONDS`, because both create offers.

- `compose.yaml`: give `api` and `worker` the line `env_file: [{path: .env, required: false}]`.
  - Settings ignores unknown keys.
  - The explicit `DATABASE_URL` in `x-app-env` still wins over `.env`.
  - Change nothing else in the file.
- `.env.example`: add `HOLD_VALIDITY_SECONDS`, `OFFER_VALIDITY_SECONDS`, `WORKER_SCAN_INTERVAL_SECONDS` and `WORKER_SCAN_BATCH_SIZE`, commented out at their defaults.

Done when: with a sample `.env`, `docker compose config` shows both validity values on both services.

## Step 9: Documents (done 2026-11-22)

Only what this branch changes:

- `doc/TECHNICAL_DESIGN.md`, version 1.5, with a "Changes in 1.5" line:
  - Section 2: remove decline and leave from the work that remains. Add the latency report and the acceptance tests.
  - Section 5: add `cli/promotion_report.py` to the tree.
  - Section 5.1: add the report command.
  - Section 7.3: add the repeat rule and the no-conversion rule.
  - Section 8.4: "repeated terminal command" now means the command's own end reason is recorded.
  - Section 15: no alias, Python 3.14, and `ACCEPTANCE_ROUNDS`.
- `doc/DATABASE_DESIGN.md`, version 1.6 (it was already 1.5): in "Confirm, cancel, decline or leave", step 5 states the repeat rule.
- `doc/openapi.yaml`: update the descriptions of cancel, decline, leave and administrator cancel.
  - State the repeat rule.
  - List the 409 codes each command can return.
  - Note that `PARTICIPATION_EXPIRED` and `REGISTRATION_CLOSED` commit the expiry or closure first.
- This file: one progress entry per finished step, with test counts and evidence figures.

Done when: every route, variable and module these documents name exists in the code.

## Step 10: Final verification and demo

1. In a fresh clone, run `uv sync`, the full suite on Python 3.14, and `uv run ruff check .`.
2. `tests/concurrency` passes five consecutive runs, and `ACCEPTANCE_ROUNDS=10` passes once.
3. Run a Compose smoke test with `HOLD_VALIDITY_SECONDS=20` and `OFFER_VALIDITY_SECONDS=20` in `.env`:
   - An organizer creates a capacity-1 session. A books; B, C and D join.
   - A's hold expires, and B is offered (check the worker log).
   - B declines, and C is offered.
   - D leaves.
   - C confirms.
   - Check states, positions and versions through `my-participation` and the roster, then run `python -m app.cli.promotion_report`.
4. `docker compose stop worker` ends within the 20 s grace period and logs "worker stopped".
5. Remove the demo values from `.env`, and record the results in the progress log.

## Accepted without change

- A capacity increase does not expire overdue attempts first, so it may offer fewer seats than are about to become free. The expiry scan closes that gap within about a second.
- Promotions inside attendee and administrator commands have no live latency metric. Step 5 covers them offline.
- For decline and leave, `SESSION_STARTED` stays as a guard, even though valid data cannot reach it there (Step 2, row 7).

## Later, after teammates' work lands

These are my own follow-ups. They wait for code that others own.

- Pass the real publisher to the scan loops instead of `publish_nothing`. Then check one expiry, one closure and one promotion end to end.
- Switch the worker from `logging.basicConfig` to the shared logging module.
- Review the worker Deployment against the probe table in TECHNICAL_DESIGN 13.3.
- Run the HTTP version of the PRD round against the deployed stack.

## Definition of done

- Every step's "Done when" holds, and the suite and Ruff are green from a fresh clone on Python 3.14.
- Five consecutive concurrency runs and one 10-round acceptance run are recorded.
- Locally, both promotion categories have at least 100 observations, with P95 at most 5 s.
- The documents match the code. Jiawei has reviewed Steps 1 to 3 and Step 6, which touch his files or validate his commands.

## Messages to send when the PR opens

- **Jiawei**:
  - The shared release command and the change in cancel behavior.
  - The `httpx2` import and the Ruff configuration.
  - A request to review the acceptance tests.
  - Also: the API's `/health/ready` returns 503 when Redis is down, which conflicts with TECHNICAL_DESIGN 13.3.
- **Yifeng**: Compose now passes `.env` to the API and the worker.
- **Everyone**: run `uv sync` after the merge, because the virtual environment is rebuilt for Python 3.14. Demo values in `.env` no longer affect the tests.
