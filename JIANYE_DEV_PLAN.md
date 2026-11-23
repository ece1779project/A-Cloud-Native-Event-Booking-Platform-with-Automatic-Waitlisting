# Development Plan: Waitlist and Workers (Jianye)

Date: 2026-11-22 · Branch: `jianye/decline-leave` · Based on `main` at `d14b99f`

This plan covers only my own work that needs nothing from teammates. It touches their files only where that work needs it, which they agreed to. The rules this work settled live in the documents, not here:

- The shared release command, its check order, the repeat rule and the no-conversion rule are in [TECHNICAL_DESIGN](doc/TECHNICAL_DESIGN.md) Section 7.3.
- The accepted limitations are in Section 18 of the same document.
- How to run the suite and the acceptance rounds is in Section 15 of the same document.

## Progress log

### Part 1: Promotion, expiration and workers (2026-11-16 to 2026-11-17, merged in #8)

- **Coordination.** Recorded service boundaries, lock and transaction rules, and team ownership in the technical design, the database design and the project proposal.
- **Shared types and helpers.** Added `SessionChange`, the participation transition and lookup helpers, and factories that write valid history. 41 tests.
- **Promotion and expiration.** Added FIFO offers, hold and offer expiry, and registration closure. An expiry and its replacement offers share one transaction and one version bump. 59 tests.
- **Workers.** Added the Compose worker with the expiry, closure, promotion and capacity scans and their metrics. All 75 tests and Ruff passed, and the race tests passed five consecutive runs. A smoke run verified expiry, replacement offers, capacity-violation reporting and graceful shutdown.

### Part 2: Decline, leave and validation (2026-11-22, Steps 1 to 10)

Planned after Jiawei's booking merge (#10). Decline and leave needed no migration, because the state machine, the trigger and the check constraints already allowed both transitions.

1. **Baseline.** Tests import `httpx2`, the package `uv.lock` pins, and build `Settings` without `.env`. Ruff treats FastAPI's `Depends(...)` family as immutable defaults and allows `dict(...)` rows in tests. `.python-version` is 3.14.
2. **Decline and leave.** Cancel, decline and leave share `_release` in `services/participations.py`, and administrator cancel follows the same repeat rule. `POST /participations/{id}/decline` and `/leave` require `Idempotency-Key` and count toward the write limit.
3. **Tests.** Added `test_decline_leave.py` and rows in the authorization matrix and the contract test: 45 tests, covering every check in the release order. 273 tests in all.
4. **Races.** Moved `fifo_violations` to `tests/helpers.py`. `test_release_races.py` covers six races: decline against expiry, decline against confirm, the queue head leaving during a cancel, concurrent declines, waiters leaving while newcomers join, and retries with one key.
5. **Latency report.** `python -m app.cli.promotion_report` pairs releases with offers by correlation ID in `participation_changes`.
6. **Acceptance.** `test_acceptance.py` runs the PRD capacity round (capacity 20, 100 clients, 1,000 commands per round), waitlist fairness, a replayed old cancellation, two worker replicas and promotion latency. 288 tests in all.
7. **Publication matrix.** `test_session_changes.py` runs 19 real operations. Each case checks the cause, the transition reasons, and one version bump per changed session.
8. **Compose.** `api` and `worker` read an optional `.env`, so demo validity values reach both processes; the `DATABASE_URL` that Compose assembles still wins. `.env.example` lists the validity and worker scan variables.
9. **Documents.** The technical design (1.5), the database design (1.6) and the OpenAPI descriptions now state the release rules, the report and the acceptance tests. 307 tests in all.
10. **Final verification.** All four checks passed; the evidence follows.

Step 10 evidence, on an Apple M4 Pro (14 cores, 24 GB) with PostgreSQL 18.6 in Docker:

- **Fresh clone of `85d7a40`.** After `uv sync`, all 307 tests passed on Python 3.14.6 with no alias, and Ruff was clean.
- **Concurrency.** `tests/concurrency` passed five consecutive runs (27 tests, about 45 s each). An earlier single failure, which could not be reproduced, did not recur.
- **Ten acceptance rounds.** `ACCEPTANCE_ROUNDS=10` showed no capacity violation, duplicate active attempt or FIFO violation in any round. Promotion latency, n=120 per category:

  | Category | P95 | Max |
  | --- | --- | --- |
  | Expiration | 1.02 s | 1.07 s |
  | Cancellation | 0.004 s | 0.008 s |

  Both meet the PRD target of n ≥ 100 and P95 ≤ 5 s.
- **Compose demo with 20 s validity.** A booked, then B, C and D joined. A's hold expired, and the worker offered B 0.6 s after the deadline, with a null actor and a shared correlation ID. B declined and C was offered, D left, and C confirmed. `my-participation`, the roster, versions 2 to 9 with no gap, and the report all matched. The worker writes no log line per expiry, so the history rows serve as evidence.
- **Worker stop.** `docker compose stop worker` took 0.3 s and logged "worker stopped". The demo values were removed from `.env`.

## Later, after teammates' work lands

- Pass the real publisher to the scan loops instead of `publish_nothing`, then check one expiry, one closure and one promotion end to end.
- Switch the worker from `logging.basicConfig` to the shared logging module, and log each expiry and offer.
- Review the worker Deployment against the probe table in TECHNICAL_DESIGN 13.3.
- Run the HTTP version of the PRD round against the deployed stack.

## Messages to send when the PR opens

- **Jiawei.** Please review Steps 1 to 3 and Step 6, which touch his files or validate his commands. He should also know about:
  - The shared release command.
  - The `httpx2` import and the Ruff configuration.
  - A cancel behavior change. Cancel on an attempt ended by a session or event cancellation now returns `409 SESSION_CANCELLED` or `409 EVENT_CANCELLED` instead of `200`. On a withdrawn or declined attempt it returns `409 INVALID_STATE_TRANSITION`.
  - The API's `/health/ready` returns 503 when Redis is down, which conflicts with TECHNICAL_DESIGN 13.3.
- **Yifeng.** Compose now passes `.env` to the API and the worker.
- **Everyone.** Run `uv sync` after the merge, because the virtual environment is rebuilt for Python 3.14. Demo values in `.env` no longer affect the tests.
