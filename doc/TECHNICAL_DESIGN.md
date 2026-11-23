# Technical Design Document: Cloud-Native Event Booking Platform

Version: 1.6  
Date: 2026-11-22  
Status: Proposed target design; implementation pending

Changes in 1.6: management commands (event, session and administrator account) now require `Idempotency-Key` and store a receipt; only a success is stored, a rejection rolls back and leaves the key unused (Section 7.5). `app.cli.export_openapi` is implemented. `INTERNAL_ERROR` (`500`) is listed for unexpected failures (Section 8.5). Section 2 lists the completed account, management and booking work.

Changes in 1.5: decline and leave are implemented on the shared cancel path. A command on an ended attempt returns `200` unchanged only when its own end reason is recorded, and a command never turns into another (Sections 7.3 and 8.4). The promotion latency report and the in-process acceptance tests are added (Sections 2, 5, 5.1 and 15). Compose passes `.env` to the API and the worker (Section 13.1). Three accepted limitations are added (Section 18).

Changes in 1.4: an enabled account is the only eligibility rule in this release, so `INELIGIBLE` is reserved and unused (Section 7.4).

Changes in 1.3: the transaction owner increments the session version once, after every step and before commit (Sections 6.3 and 7). Commands on an existing participation first apply `expire_if_overdue` and `end_waiting_if_closed`, and the operations that run `promote_waiters` are listed (Sections 7.3 and 7.4). System transitions record a null `actor_id`, disabled waiters end as `CANCELLED` with `ACCOUNT_DISABLED`, and all overdue attempts of one session are handled in one transaction (Sections 7.4 and 11).

Changes in 1.2: a command that advances a session version returns `SessionChange`, and the publisher accepts that value after commit (Sections 5.2 and 10.3). The worker process serves health and metrics on `WORKER_METRICS_PORT`, and a gauge records the last completed scan (Sections 13.3, 13.5, and 14).

Changes in 1.1: aligned with the draft contract in [openapi.yaml](openapi.yaml). Management commands require `Idempotency-Key`; new codes `EMAIL_ALREADY_REGISTERED` and `SCHEDULE_FROZEN`; event publication also publishes sessions added later; `GET /me/events` lists manageable events; hidden drafts return 404 on management routes; event titles stay editable after publication; administrators cannot grant `ADMIN`; WebSocket ticket scopes are named (Section 10.5).

## 1. Purpose

This document describes the planned technical implementation of the cloud-native event booking platform. It covers the system architecture, service responsibilities, data and concurrency model, API and real-time communication, security, deployment, observability, and delivery sequence.

The design is based on [Project_Proposal.md](Project_Proposal.md), [PRD.md](PRD.md), and [DATABASE_DESIGN.md](DATABASE_DESIGN.md). PostgreSQL remains the source of truth for booking state, while Redis supports rate limiting and real-time delivery without becoming part of the core correctness boundary.

Requirement identifiers such as FR-04 and NFR-01 refer to the PRD.

## 2. Implementation status

The following work remains to be implemented:

- Post-commit notifications: the routes and services hand `SessionChange` values to `publish_after_commit`, which publishes nothing until `app.realtime.publisher` exists.
- WebSocket gateway, real-time ticket issuance (`POST /realtime/tickets`) and frontend.
- The worker's health and metrics server on `WORKER_METRICS_PORT`.
- Kubernetes manifests, migration jobs, monitoring, dashboards, alerts, or load tests.

The package skeleton in Section 5 is in place: every module exists with a statement of its responsibility. The database layer is implemented and tested: settings, the engine, the SQLAlchemy models, both Alembic revisions, the lock entry point, the participation store, and the `/health/live` and `/health/ready` endpoints. Compose runs the migration before it starts the API.

Promotion and expiration are implemented and tested: the steps in `services/promotion.py` and `services/expiration.py` (Sections 7.3 and 7.4), the `SessionChange` result in `schemas/realtime.py` (Section 10.3), the four worker scans with their loop, shutdown and metrics (Sections 11 and 14), and a `worker` service in Compose. Workers publish nothing until the publisher exists.

Accounts and the API are implemented and tested: registration, login with refresh rotation and logout, the current account, role checks, administrator account changes and the bootstrap command (Sections 8.2 and 9); Redis write rate limiting with the per-replica fallback; event and session management with publication, schedule freeze, capacity changes and cancellation (Section 8.3); booking, waitlist join, confirmation, cancellation, administrator cancellation and the stored-result lookup (Sections 7.2, 7.3 and 8.4). Every authenticated business command, attendee and management alike, requires `Idempotency-Key` and stores its receipt in the transaction with its effects (Section 7.5); `python -m app.cli.export_openapi` writes the generated contract (Section 8.1).

Decline and leave are implemented and tested. They share one release command with attendee cancel (Section 7.3). `python -m app.cli.promotion_report` measures promotion latency offline from `participation_changes` (Section 14). `tests/concurrency/test_acceptance.py` runs the PRD capacity rounds and the other joint validation scenarios in process (Section 15); the HTTP load test waits for the deployment.

The remaining sections describe planned behavior rather than completed or measured results.

## 3. Goals, constraints, and decisions

### 3.1 Design goals

- Never allocate more occupied seats than a session's capacity.
- Preserve one active participation per account and session.
- Promote eligible attendees in persistent FIFO order.
- Make retries, duplicate tasks, process restarts, and lost responses safe.
- Keep booking writes correct and available when Redis is unavailable.
- Recover authoritative UI state after lost or out-of-order notifications.
- Scale stateless API and WebSocket processes without moving correctness out of PostgreSQL.
- Produce evidence for the PRD's concurrency, latency, recovery, and scaling acceptance targets.

### 3.2 Non-goals

The first release does not implement real payments, refunds, seat selection, ticket types, group bookings, transfers, check-in, scheduling conflict detection, priority queues, email or SMS delivery, multi-region recovery, automatic PostgreSQL failover, or inventory pre-deduction in Redis.

### 3.3 Resolved technical decisions

- Backend: Python 3.14, FastAPI, Pydantic, SQLAlchemy 2.0 async, asyncpg, and Alembic.
- Frontend: React, TypeScript, and Vite as a dynamic browser SPA built into static deployable assets.
- Authentication: short-lived signed JWT access tokens and rotating opaque refresh tokens in an `HttpOnly` cookie.
- Reliable work: PostgreSQL scans and idempotent database transactions.
- Real-time delivery: Redis Pub/Sub as best-effort invalidation; REST remains authoritative.
- Concurrency: PostgreSQL `READ COMMITTED` plus a common event/session/account row-locking entry point.
- Public routing: DOKS-managed Gateway API using the `cilium` GatewayClass.
- Local orchestration: Docker Compose; Kubernetes validation: minikube; cloud: DOKS.

## 4. System architecture

### 4.1 System context

```mermaid
flowchart LR
    attendee[Attendee]
    organizer[Organizer]
    administrator[Administrator]
    browser[ReactBrowserApp]
    platform[TicketbookingPlatform]
    doMonitoring[DigitalOceanMonitoring]

    attendee --> browser
    organizer --> browser
    administrator --> browser
    browser -->|"HTTPS and WSS"| platform
    platform -->|"Infrastructure metrics"| doMonitoring
```

### 4.2 Runtime containers

```mermaid
flowchart LR
    browser[ReactBrowserApp]
    gateway[GatewayAPI]
    frontend[ReactSPA]
    api[FastAPIService]
    websocket[WebSocketGateway]
    worker[BackgroundWorkers]
    postgres[(PostgreSQL)]
    redis[(Redis)]
    prometheus[Prometheus]
    grafana[Grafana]

    browser -->|"GET /"| gateway
    browser -->|"REST /api/v1"| gateway
    browser -->|"WSS /ws/v1"| gateway
    gateway --> frontend
    gateway --> api
    gateway --> websocket
    api -->|"Authoritative reads and writes"| postgres
    worker -->|"Scans and transactions"| postgres
    websocket -->|"Authorization and reconciliation"| postgres
    api -->|"Rate limits and post-commit publish"| redis
    worker -->|"Post-commit publish"| redis
    websocket -->|"Pub/Sub and tickets"| redis
    prometheus --> api
    prometheus --> websocket
    prometheus --> worker
    prometheus --> postgres
    prometheus --> redis
    grafana --> prometheus
```

### 4.3 Component responsibilities

**React SPA frontend**

- Provides event list, event details, My Registrations, and management pages.
- Loads as static HTML, JavaScript, and CSS, then behaves dynamically in the browser through REST state changes and WebSocket updates.
- Sends commands through REST and treats REST responses as authoritative.
- Uses WebSocket messages as invalidation signals, not as the sole copy of business state.
- Discards state older than the highest displayed session version.

**FastAPI service**

- Owns HTTP authentication, RBAC and ownership checks.
- Exposes public queries, attendee commands, and management operations.
- Applies rate limits before business transactions.
- Runs all business writes through the shared locking and command layer.
- Publishes sanitized invalidation messages only after a successful commit.

**Background workers**

- Scan PostgreSQL for overdue holds and offers, closed registrations, and sessions needing promotion.
- Reacquire the same locks as API writers and recheck every candidate.
- Process bounded batches safely across multiple replicas.
- Export backlog, latency, capacity-invariant metrics, and the time of the last completed scan.

**WebSocket gateway**

- Upgrades browser connections using short-lived, single-use connection tickets.
- Maintains public session subscriptions and authenticated private subscriptions.
- Consumes Redis Pub/Sub messages and sends small versioned invalidation events.
- Reauthorizes private delivery and closes expired or disabled sessions.
- Does not decide booking outcomes or retain authoritative booking state.

**PostgreSQL**

- Owns accounts, refresh sessions, events, sessions, participation attempts, queue order, deadlines, idempotency receipts, and audit history.
- Serializes writes affecting one event session.
- Supplies database time for deadline decisions.

**Redis**

- Stores rate-limit counters and one-time WebSocket tickets.
- Fans out post-commit invalidations.
- May be lost without losing booking facts. Redis persistence improves restart behavior but is not a correctness boundary.

## 5. Repository structure

The API, workers, and WebSocket gateway share one Python package and image. Different container commands start different process types. Each module carries a docstring that states its responsibility and the section of this document it implements. Section 2 lists which modules are implemented; the others are still skeletons.

```text
.
├── app/                              # One package, one image, three process types
│   ├── main.py                       # API process: FastAPI factory and lifecycle
│   ├── api/                          # HTTP transport only
│   │   ├── dependencies.py           # Authentication, DB, request context, rate limits
│   │   ├── exception_handlers.py     # Error envelope (Section 8.1)
│   │   ├── middleware.py             # Request ID, access log, HTTP metrics
│   │   ├── health.py                 # Health and metrics endpoints (Section 13.3)
│   │   └── v1/                       # One module per route group in Section 8
│   │       ├── router.py             # The only place a route module is registered
│   │       ├── accounts.py
│   │       ├── auth.py
│   │       ├── events.py
│   │       ├── sessions.py
│   │       ├── participations.py
│   │       ├── admin.py
│   │       └── realtime.py           # Ticket issuance only
│   ├── core/                         # Infrastructure with no business knowledge
│   │   ├── config.py                 # Settings (Section 13.5)
│   │   ├── constants.py              # Values fixed by this design
│   │   ├── errors.py                 # Exceptions and stable error codes
│   │   ├── logging.py
│   │   ├── metrics.py
│   │   ├── security.py               # Password hashing, token signing, digests
│   │   ├── redis_client.py
│   │   ├── redis_keys.py             # Every Redis name (Section 10.5)
│   │   └── rate_limit.py             # Redis limiter and in-process fallback
│   ├── domain/                       # Pure rules: no I/O, no imports from app
│   │   ├── enums.py                  # Roles, statuses, states, reason and result codes
│   │   ├── transitions.py            # Participation state machine
│   │   └── timing.py                 # Registration window and deadline rules
│   ├── db/
│   │   ├── base.py                   # DeclarativeBase and naming convention
│   │   ├── engine.py                 # Engine, session factory, timeouts, database clock
│   │   ├── locks.py                  # The only business-write lock entry point
│   │   └── participation_store.py    # Participation reads and low-level writes
│   ├── models/                       # One module per table
│   │   ├── account.py
│   │   ├── auth_session.py
│   │   ├── event.py
│   │   ├── event_session.py
│   │   ├── participation.py
│   │   ├── participation_change.py
│   │   ├── request_result.py
│   │   └── management_action.py
│   ├── schemas/                      # Pydantic contracts (Sections 8.6 and 10.1)
│   │   ├── common.py                 # Envelopes, meta, pagination cursor
│   │   ├── accounts.py
│   │   ├── auth.py
│   │   ├── events.py
│   │   ├── sessions.py
│   │   ├── participations.py
│   │   └── realtime.py               # WebSocket messages (Section 10.1) and SessionChange (Section 10.3)
│   ├── services/                     # Transactional command and query services
│   │   ├── accounts.py
│   │   ├── auth.py
│   │   ├── events.py
│   │   ├── sessions.py               # Includes capacity change and roster
│   │   ├── booking.py                # Direct booking and waitlist join
│   │   ├── participations.py         # Confirm, cancel, decline, leave, queries
│   │   ├── promotion.py              # Promote steps, shared with workers
│   │   ├── expiration.py             # Expiry and registration closure
│   │   ├── idempotency.py            # Command receipts
│   │   └── audit.py                  # Management-action writer
│   ├── realtime/
│   │   ├── publisher.py              # Post-commit publication (API and workers)
│   │   ├── tickets.py                # Issue (API) and consume (gateway)
│   │   └── gateway/                  # WebSocket gateway process
│   │       ├── main.py               # Gateway entry point
│   │       ├── connection.py         # Upgrade, heartbeat, revalidation, close codes
│   │       ├── subscriptions.py      # Channel parsing, authorization, limits
│   │       └── fanout.py             # Redis pattern subscription and routing
│   ├── workers/                      # Background worker process
│   │   ├── __main__.py               # Worker entry point
│   │   ├── runner.py                 # Scan loop: interval, batch, backoff, shutdown
│   │   ├── expiry.py                 # Scan 1 in Section 11
│   │   ├── closure.py                # Scan 2
│   │   ├── promotion.py              # Scan 3
│   │   └── invariant.py              # Scan 4
│   └── cli/                          # One-off commands
│       ├── bootstrap_admin.py
│       ├── export_openapi.py
│       └── promotion_report.py       # Promotion latency from history (Section 14)
├── migrations/                       # Alembic environment and revisions
├── alembic.ini
├── frontend/                         # Created by the Vite scaffold in Phase 4
│   ├── src/
│   │   ├── api/
│   │   ├── auth/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── realtime/
│   │   └── routes/
│   └── package.json
├── deploy/
│   ├── kubernetes/
│   │   ├── base/
│   │   └── overlays/
│   │       ├── minikube/
│   │       └── doks/
│   ├── monitoring/                   # Prometheus, Grafana, alert rules
│   └── scripts/
├── tests/
│   ├── conftest.py                   # PostgreSQL fixtures shared by integration and concurrency
│   ├── factories.py                  # Row builders that bypass business rules
│   ├── unit/                         # domain, core, schema validation; no database
│   ├── integration/                  # Services and routes against PostgreSQL and Redis
│   ├── concurrency/                  # Races over independent connections
│   ├── contract/                     # OpenAPI, codes, message fixtures, Redis names
│   │   └── fixtures/
│   └── load/
├── compose.yaml
├── Dockerfile
└── pyproject.toml
```

`frontend/` is absent until the Vite scaffold runs, because that generator expects an empty target directory.

### 5.1 Process entry points

| Process | Command | Module |
| --- | --- | --- |
| API | `uvicorn app.main:app` | `app/main.py` |
| WebSocket gateway | `uvicorn app.realtime.gateway.main:app` | `app/realtime/gateway/main.py` |
| Workers | `python -m app.workers` | `app/workers/__main__.py` |
| Administrator bootstrap | `python -m app.cli.bootstrap_admin` | `app/cli/bootstrap_admin.py` |
| OpenAPI export for CI | `python -m app.cli.export_openapi` | `app/cli/export_openapi.py` |
| Promotion latency report | `python -m app.cli.promotion_report [--since ISO-8601] [--session UUID]` | `app/cli/promotion_report.py` |

### 5.2 Layering rules

A module imports only from layers below its own:

```text
api, workers, realtime/gateway, cli       entry layers
services                                  business transactions
realtime/publisher, realtime/tickets      post-commit Redis side effects
schemas, models, db                       contracts and persistence
domain, core                              leaves; import nothing from app
```

- Route handlers validate transport data, call one service, and, when that service returns a `SessionChange`, publish it after the call returns. They open no transaction and take no lock.
- Services own authorization-sensitive business decisions and transactions. A service is called by route handlers and by workers, knows nothing about HTTP, and never imports `app.api`, `app.workers`, or `app.realtime`.
- The service that owns the transaction returns `None`, one `SessionChange`, or a list of them when the transaction advanced several session versions. `None` and an empty list mean that no session version changed. The fields are `event_id`, `session_id`, `version`, `cause`, and `transitions`, and the type is defined in Section 10.3. The caller, a route handler or a worker loop, passes the returned values to `realtime/publisher.py` after the service returns. `None` and an empty list are not published. The service does not build WebSocket messages, and publication never runs inside a transaction.
- The transaction owners are `services/booking.py`, `services/participations.py`, `services/sessions.py`, `services/events.py`, and `services/expiration.py`. An event publish or an event cancel returns one `SessionChange` per session version it incremented. `services/promotion.py` only applies steps inside the caller's transaction, so it does not return a `SessionChange` and does not publish. A simulated payment failure, an event-field edit, an account disable, and a replay that writes nothing return `None`.
- Models hold columns, constraints, and indexes only. Models never publish messages.
- `db/participation_store.py` holds the participation queries and low-level writes that several services share: occupancy counts, the queue head, waitlist position, the queue number, the session version, and the history row. It makes no business decision, and its writes assume the caller already holds the session lock from `db/locks.py`.
- PostgreSQL triggers reject an illegal state transition and any edit of history or frozen fields (`DATABASE_DESIGN.md` Section 2.6). They are a last line of defense: a service checks the rule first and returns the stable error code, so a trigger rejection that reaches a client is a bug.
- `domain` holds the state machine and time rules as pure functions. Models, schemas, services, workers, and the gateway share its enumerations, and unit tests cover it without a database.
- A worker scan only selects candidates. Each candidate is handled through the same service and lock entry point as an API command.
- The gateway reads PostgreSQL only for the account-state and ownership checks in Section 10.2 and never calls a service that writes.

The modules, schemas, routes, and services for one resource share a name, for example `api/v1/events.py`, `schemas/events.py`, `services/events.py`, and `models/event.py`. A new resource adds one module to each layer and one line to `api/v1/router.py`. A new scan adds one module to `workers/` and registers it in `workers/__main__.py`.

## 6. Domain and persistence design

### 6.1 Core records

The schema in `DATABASE_DESIGN.md` remains the baseline:

- `accounts`
- `events`
- `event_sessions`
- `participations`
- `participation_changes`
- `request_results`
- `management_actions`

The participation states are exactly `HELD`, `WAITLISTED`, `OFFERED`, `CONFIRMED`, `CANCELLED`, and `EXPIRED`. Only `HELD`, `OFFERED`, and `CONFIRMED` occupy seats.

The following invariant is checked in transactions, tests, and monitoring:

```text
count(HELD) + count(OFFERED) + count(CONFIRMED) <= event_sessions.capacity
```

The partial unique index on active `(session_id, account_id)` prevents multiple active attempts for one attendee. The unique `(session_id, queue_seq)` index preserves queue identity across terminal attempts.

Direct holds last five minutes by default. Waitlist offers last ten minutes by default. In both cases the persisted deadline is:

```text
deadline_at = min(allocation_time + configured_validity, registration_closes_at)
```

The validity settings affect new allocations only; they never rewrite an existing deadline.

### 6.2 Authentication persistence addendum

Authentication requires one additional table. Its columns, constraints, and indexes are specified in Section 2.5 of `DATABASE_DESIGN.md` (design version 1.2); the summary below explains its behavior:

`auth_sessions`

- `id uuid` primary key.
- `account_id uuid` references `accounts.id` with `ON DELETE RESTRICT`.
- `refresh_token_hash bytea` is a unique SHA-256 digest of a random 256-bit token; the plaintext token is never stored.
- `family_id uuid` groups tokens descended from one login.
- `csrf_token_hash bytea` binds refresh and logout requests to the browser session.
- `created_at`, `last_used_at`, and `expires_at` are `timestamptz`.
- `rotated_at`, `revoked_at`, and `revoke_reason` are nullable.
- `user_agent_hash bytea` and `last_ip_prefix text` support investigation without storing unnecessary raw client data.

Indexes cover `(account_id, expires_at)` and unexpired token lookup. Refresh rotation locks the presented row, marks it rotated, and inserts its successor in one transaction. Every successor keeps the family's original absolute expiry and CSRF digest; rotation does not extend the login lifetime. A second concurrent refresh received within a five-second grace window returns `409 REFRESH_ALREADY_ROTATED` without revoking the family. Reuse after that window is treated as theft and revokes the family. Disabling an account revokes its refresh sessions in the same management transaction.

This table must be included in the first Alembic revision.

### 6.3 Time and versions

- Store all instants as `timestamptz` and use UTC database connections.
- Preserve an IANA display time zone on each event session.
- Use `clock_timestamp()` after all blocking locks for registration and deadline decisions.
- Never use browser time, API process time, or PostgreSQL transaction-start `now()` to decide whether a hold can confirm.
- Increment `event_sessions.version` in every committed transaction that changes capacity, status, or any participation in the session.
- The transaction owner (Section 5.2) increments the version exactly once per session per transaction, after all business steps and immediately before commit. Helper steps such as `promote_waiters`, `expire_if_overdue`, and `end_waiting_if_closed` neither commit nor increment the version. A transaction that changes nothing leaves the version unchanged and returns `None`.
- Publication of the resulting `SessionChange` happens only after commit (Section 10.3).
- Include `session_version` in session reads, participation reads, command results, and real-time events.

## 7. Transaction and concurrency design

### 7.1 Common lock entry point

All API and worker writes that can affect participation, capacity, queue order, or session status call one shared function in `app/db/locks.py`. Direct locking elsewhere is prohibited.

For a normal session command:

1. Read an existing idempotency receipt as a non-locking fast path.
2. Lock the parent event `FOR SHARE`.
3. Lock the event session `FOR UPDATE`.
4. Recheck the receipt, then read occupancy, queue, and active attempts.
5. Lock accounts whose role or eligibility is used with shared locks, in ascending account-ID order.
6. Read `clock_timestamp()` after all potentially blocking locks.
7. Validate and write the transition, history, version, and receipt in one transaction.

Whole-event edits use an exclusive event lock. Multi-session operations lock sessions in ascending UUID order. Account role or disablement changes use an exclusive account lock. Promotion is the documented exception to ascending account-ID order: the session lock fixes the queue, and each queue-head account is taken with a shared lock; role and disablement commands lock only their one target account exclusively, so they cannot form a cycle with promotion. The connection settings are:

- `lock_timeout = 5s`
- `statement_timeout = 10s`
- `idle_in_transaction_session_timeout = 15s`

A lock timeout rolls back and returns a retryable `503 LOCK_TIMEOUT`. It does not consume the idempotency key.

### 7.2 Direct booking and waitlist join

Booking under the session lock:

1. Return an existing active attempt without creating another one.
2. Require published event and session status and an open registration window.
3. Count occupied seats and active waiters.
4. If a seat is free and the queue is empty, insert `HELD`.
5. Otherwise return `WAITLIST_REQUIRED`; never enqueue implicitly.

Joining the waitlist under the same lock:

1. Return an existing active attempt without changing its order.
2. Require an enabled account, published event and session, and an open registration window.
3. If direct booking is possible, return `DIRECT_BOOKING_AVAILABLE`.
4. Require waitlisting to be necessary because the session is full or an active queue exists.
5. Increment `last_queue_seq` and insert `WAITLISTED` atomically.

### 7.3 Confirmation and expiration race

Confirmation locks the session and attempt, then reads database time. If `t >= deadline_at`, it first expires the attempt and promotes from the queue in the same transaction; confirmation returns `PARTICIPATION_EXPIRED`. Otherwise a successful simulation changes `HELD` or `OFFERED` to `CONFIRMED` without changing occupancy. A simulated failure changes no participation state and leaves the original deadline intact.

Every command on an existing participation (confirm, cancel, decline, leave, and administrator cancel) runs two steps from `services/expiration.py` after the locks and clock read and before its own validation:

1. `expire_if_overdue`: a `HELD` or `OFFERED` attempt whose deadline has passed becomes `EXPIRED` with `HOLD_EXPIRED` or `OFFER_EXPIRED`.
2. `end_waiting_if_closed`: a `WAITLISTED` entry in a session whose registration has closed becomes `CANCELLED` with `REGISTRATION_CLOSED`.

The command then validates against the resulting state, so an attendee acting late meets the same outcome the worker would have written.

Attendee cancel, decline, and leave differ only in the states they accept (`HELD` or `CONFIRMED`, `OFFERED`, `WAITLISTED`), the end reason they write (`USER_CANCELLED`, `OFFER_DECLINED`, `WITHDRAWN`), and whether they free a seat (leave does not). One function in `services/participations.py` implements all three, so they share the check order and the two rules below.

The check order: someone else's attempt returns `404` and stores nothing; a receipt that committed while the command waited is replayed; an expiry or closure written by the two steps above returns its `409` and commits; an ended attempt follows the repeat rule; a confirmed booking after the session start returns `409 SESSION_STARTED`; a state outside the command's own returns `409 INVALID_STATE_TRANSITION`; otherwise the attempt ends with the caller as actor, and a freed seat is offered in the same transaction. Every `409` stores a receipt.

- Repeat rule: on an attempt that has already ended, a command returns `200` with the unchanged result only when its own end reason is recorded. Administrator cancel follows the same rule with `ADMIN_CANCELLED`. An attempt that ended any other way returns the `409` for that ending: `PARTICIPATION_EXPIRED`, `REGISTRATION_CLOSED`, `SESSION_CANCELLED`, `EVENT_CANCELLED`, or otherwise `INVALID_STATE_TRANSITION`.
- No conversion: a command never turns into another. Leave on an `OFFERED` attempt, or decline on a `HELD` one, returns `409 INVALID_STATE_TRANSITION` and changes nothing. The client refetches and shows the right action.

The worker follows the same lock path. A stale expiry candidate becomes a no-op. The session lock ensures that confirmation and expiration cannot both win.

### 7.4 Cancellation, capacity changes, and promotion

Any transaction that frees a seat or increases capacity promotes waiters before commit:

1. Recheck that registration remains open and a seat is free.
2. Select the lowest active `queue_seq`.
3. Take a shared lock on that account and check its eligibility.
4. End an ineligible entry with a recorded reason, or change it to `OFFERED`.
5. After that account lock, sample `clock_timestamp()` and recheck registration time.
6. Set `allocated_at` to that database time and set `deadline_at` to the earlier of ten minutes later and registration close.
7. Leave the version increment to the transaction owner, which increments it once before commit (Section 6.3).
8. Repeat for available seats, up to 20 offers per transaction, obtaining a fresh database time after each new blocking account lock.

These operations call `services/promotion.py::promote_waiters` before commit:

- Attendee cancel of a hold or confirmed booking, and decline of an offer.
- Administrator cancel of a participation.
- A capacity increase.
- A confirmation that finds its attempt overdue and expires it (Section 7.3).
- Expiration and closure transactions while registration remains open, and the worker promotion scan (Section 11).

Leaving the waitlist frees no seat and does not promote.

Promotion rules:

- A history row written by promotion, expiration, or closure has a null `actor_id`. Only a transition requested by an account records that account; a promotion inside an attendee's or administrator's command still records null for the promoted entry.
- A queue-head account that is disabled ends as `CANCELLED` with reason `ACCOUNT_DISABLED`; promotion then continues with the next entry. Other ineligible entries end with `INELIGIBLE`. In this release an enabled account is the only eligibility rule, so no entry ends with `INELIGIBLE`; the code is reserved for a later rule.

Do not use `SKIP LOCKED` to choose attendees within one session. It may be used only to distribute independent scan candidates across sessions. Capacity reductions are rejected below current occupancy. Existing waiters are promoted before new arrivals can directly book.

### 7.5 Idempotency

All authenticated business commands, attendee and management alike, require an `Idempotency-Key` header containing a client-generated UUID. The server scopes it by account and stores its result in `request_results`.

- The parameter hash covers the operation, target IDs, and canonical business input.
- A matching retry replays the original status and body after current access is rechecked.
- Reusing a key with different inputs returns `409 IDEMPOTENCY_CONFLICT`.
- The receipt is inserted in the same transaction as business effects.
- If a unique-key race occurs, the losing transaction rolls back completely and reads the winner in a fresh transaction.
- Lock timeouts, rate-limit rejection, and dependency failure before execution do not consume the key.
- Receipts are retained for this release, exceeding the PRD's 24-hour minimum.

For FR-10.3, “reuse” means reuse of an identifier whose command reached execution and has a stored receipt. A request rejected before execution has not claimed the identifier, so it remains available for retry.

The frontend creates a key at the start of an operation and reuses it until the outcome is resolved. On a network timeout it displays “Checking result” and queries the command result or current participation instead of generating a new operation.

### 7.6 Booking sequence

```mermaid
sequenceDiagram
    participant UI as ReactApp
    participant API as FastAPI
    participant DB as PostgreSQL
    participant Redis as Redis
    participant WS as WebSocketGateway

    UI->>API: POST book with Idempotency-Key
    API->>DB: Begin and lock event/session/account
    API->>DB: Check receipt, clock, occupancy, queue
    API->>DB: Insert HELD, history, receipt and version
    API->>DB: Commit
    DB-->>API: Commit succeeds
    API-->>UI: 201 HELD with deadline and version
    API->>Redis: Publish session invalidation
    Redis-->>WS: Versioned event
    WS-->>UI: session.changed
    UI->>API: GET authoritative session state
```

If publication fails after commit, the API response remains successful. Other clients recover through periodic reconciliation or reconnection. The system does not roll back committed business state because Redis notification failed.

## 8. HTTP API contract

### 8.1 Conventions

- Base path: `/api/v1`.
- JSON uses `snake_case` to match Python schemas.
- UUIDs are lowercase canonical strings.
- Timestamps are RFC 3339 UTC values with an explicit `Z`.
- List endpoints use an opaque, URL-safe cursor and `limit` defaulting to 20 and capped at 100. A cursor encodes the endpoint's deterministic sort key and ID tie-breaker; clients must not construct it.
- Every mutating authenticated endpoint requires `Idempotency-Key`, including event, session, and administrator commands. Login, token refresh, logout, and real-time ticket issuance are exempt. Account registration is unauthenticated and takes no key.
- A participation command response carries its business result code from Section 8.5 in `meta.result`.
- Every response includes `X-Request-ID`; a safe client-supplied value may be retained, otherwise the server creates one.
- A response concerning one session includes `session_version` in `meta`. Multi-session responses carry `version` on every session or participation item, and the client tracks versions by `session_id`.
- OpenAPI is generated by FastAPI and checked into CI as a contract artifact. Until the routes exist, [openapi.yaml](openapi.yaml) is the reviewed draft of this section; afterwards the generated document is compared against it.

Successful single-resource response:

```json
{
  "data": {},
  "meta": {
    "request_id": "uuid",
    "server_time": "2026-11-14T22:00:00Z",
    "session_version": 12
  }
}
```

Error response:

```json
{
  "error": {
    "code": "WAITLIST_REQUIRED",
    "message": "No seat can be booked directly while the session is full or queued.",
    "retryable": false,
    "details": {}
  },
  "meta": {
    "request_id": "uuid",
    "server_time": "2026-11-14T22:00:00Z",
    "session_version": 12
  }
}
```

### 8.2 Authentication and account routes

- `POST /accounts`: create an attendee account.
- `POST /auth/login`: verify credentials, return an access token, and set refresh and CSRF cookies.
- `POST /auth/refresh`: rotate the refresh token and return a new access token.
- `POST /auth/logout`: revoke the current refresh session and clear cookies.
- `POST /realtime/tickets`: create a public-only anonymous ticket, or an authenticated ticket that may include private scopes.
- `GET /me`: return the current account, role, and disabled status.
- `GET /admin/accounts`: list accounts; administrator only.
- `PATCH /admin/accounts/{account_id}`: disable or re-enable an account or switch it between `ATTENDEE` and `ORGANIZER`, with an audit reason.

Public registration always creates `ATTENDEE`; an email that already exists after normalization returns `409 EMAIL_ALREADY_REGISTERED`. The bootstrap command creates administrators. No route grants the `ADMIN` role, and an administrator cannot change their own account or another `ADMIN` account (`403 FORBIDDEN`).

### 8.3 Event and session routes

Public:

- `GET /events`: published events with keyword (title and description), open-registration, and cursor filters, sorted by nearest upcoming published session start and then event ID; events without an upcoming session appear last.
- `GET /events/{event_id}`: published event details and sessions; the owner or an administrator may also retrieve a draft.
- `GET /sessions/{session_id}`: session details, availability snapshot, queue count, and version. A draft session, or a session of a draft event, is visible only to the owner and administrators.

Organizer or administrator:

- `GET /me/events`: events the caller can manage, in every status and including drafts. Organizers get their own events; administrators get all events. Sorted by creation time descending, then event ID.
- `POST /events`
- `PATCH /events/{event_id}`
- `POST /events/{event_id}/sessions`
- `PATCH /sessions/{session_id}`
- `POST /events/{event_id}/publish`
- `POST /sessions/{session_id}/cancel`
- `POST /events/{event_id}/cancel`
- `GET /sessions/{session_id}/roster`

Cancellation requests require a nonblank reason. Ownership is checked in the service layer even when the UI hides an action.

Drafts are visible only to their owner and administrators. A valid session is a `DRAFT` session whose registration close is later than the database time. Every new session starts as `DRAFT`, including one added to a published event. `POST /events/{event_id}/publish` publishes every valid draft session and, if the event is still a draft, the event itself, in one transaction; invalid draft sessions stay `DRAFT`. A draft event with no valid session returns `409 INVALID_STATE_TRANSITION`. On a published event the same call publishes sessions added since, and returns `200` unchanged when there are none. It is the only route that publishes a session.

Once any participation exists, including a terminal one, registration times, session times, and display time zone cannot change; such an edit returns `409 SCHEDULE_FROZEN`. After publication, title, description, and location may change, capacity may increase, and capacity may decrease only to current occupancy or above. A cancelled event or session cannot be edited.

Session cancellation locks and marks the session `CANCELLED`, terminalizes its active attempts, appends their histories, increments the session version, and records the management action in one transaction. Event cancellation takes an exclusive event lock, locks sessions in ascending ID order, marks the parent and sessions cancelled, terminalizes every active attempt, appends history, increments affected versions, and records one management action in the same commit. If the bounded statement timeout is exceeded, the whole transaction rolls back and returns a retryable failure; no partial cancellation is exposed. Historical participation responses derive the visible label “Event cancelled” from a cancelled parent while preserving each attempt's original transition history and reason.

`GET /sessions/{session_id}/roster` returns categorized counts and lists for `HELD`, `OFFERED`, `CONFIRMED`, and `WAITLISTED`. It uses one SQL statement/CTE or a read-only `REPEATABLE READ` transaction so counts and rows represent one snapshot. Waitlist rows include a one-based position computed only among active `WAITLISTED` rows.

### 8.4 Participation routes

- `POST /sessions/{session_id}/book`
- `POST /sessions/{session_id}/waitlist`
- `GET /sessions/{session_id}/my-participation`
- `GET /me/participations`
- `GET /participations/{participation_id}`
- `POST /participations/{participation_id}/confirm`
- `POST /participations/{participation_id}/cancel`
- `POST /participations/{participation_id}/decline`
- `POST /participations/{participation_id}/leave`
- `POST /admin/participations/{participation_id}/cancel`: administrator cancellation with a required audit reason.
- `GET /requests/{request_id}`: retrieve the caller's stored command outcome.

The confirmation body contains only `simulation_result: "success" | "failure"`. The platform does not accept card or bank data.

`GET /sessions/{session_id}/my-participation` returns the active attempt when one exists; otherwise it returns the latest terminal attempt and marks it as historical. `GET /me/participations` returns all attempts, prioritizing active `HELD` and `OFFERED` records by deadline.

Command outcomes are fixed as follows:

- A new hold or waitlist entry returns `201`.
- An existing active participation or a repeated terminal command returns `200` with the unchanged current result. A terminal command is repeated only when its own end reason is already recorded (Section 7.3).
- Successful confirmation, cancellation, decline, or leave returns `200`.
- Simulated payment failure returns `200 PAYMENT_SIMULATION_FAILED` and preserves the state and deadline.
- `WAITLIST_REQUIRED` and `DIRECT_BOOKING_AVAILABLE` return `409` with current availability and version; they do not consume capacity or create participation.
- Expired, closed, cancelled, started, or invalid-transition commands return `409`.

Private records owned by another attendee return `404`, not `403`. A draft event or session the caller cannot see returns `404` on every route, management routes included, so a response never reveals that the draft exists. Management endpoints return `403` when the caller can see the resource but lacks the required role or ownership.

A simulated payment failure is a resolved outcome. A new payment attempt uses a new `Idempotency-Key`; reusing the old key replays the stored failure.

### 8.5 Stable result and error codes

Success and business result codes:

- `HELD`, `WAITLISTED`, `OFFERED`, `CONFIRMED`, `CANCELLED`, `EXPIRED`
- `PAYMENT_SIMULATION_FAILED`
- `EXISTING_ACTIVE_PARTICIPATION`

`WAITLIST_REQUIRED` and `DIRECT_BOOKING_AVAILABLE` are business rejections, not results: they use the error envelope with status `409` below.

Client and authorization errors:

- `VALIDATION_ERROR` (`422`)
- `AUTHENTICATION_REQUIRED`, `INVALID_CREDENTIALS`, `TOKEN_EXPIRED` (`401`)
- `FORBIDDEN`, `ACCOUNT_DISABLED` (`403`)
- `RESOURCE_NOT_FOUND` (`404`)
- `IDEMPOTENCY_CONFLICT`, `INVALID_STATE_TRANSITION` (`409`)
- `REGISTRATION_NOT_OPEN`, `REGISTRATION_CLOSED`, `SESSION_CANCELLED`, `EVENT_CANCELLED` (`409`)
- `PARTICIPATION_EXPIRED`, `SESSION_STARTED`, `CAPACITY_BELOW_OCCUPIED`, `SCHEDULE_FROZEN` (`409`)
- `EMAIL_ALREADY_REGISTERED` (`409`)
- `WAITLIST_REQUIRED`, `DIRECT_BOOKING_AVAILABLE`, `REFRESH_ALREADY_ROTATED` (`409`)
- `RATE_LIMITED` (`429`, with `Retry-After`)

Dependency and concurrency errors:

- `LOCK_TIMEOUT` (`503`, retryable)
- `DATABASE_UNAVAILABLE` (`503`, retryable)
- `SERVICE_UNAVAILABLE` (`503`, retryable)
- `INTERNAL_ERROR` (`500`, not retryable): any unexpected exception; the response carries no internal detail

Expected business rejections are not logged as server errors and are counted separately from unexpected `5xx` responses.

### 8.6 Schema and validation contract

The OpenAPI components use these stable shapes; implementation may add read-only fields without changing their meanings:

- `AccountCreate`: `email`, `display_name`, and `password`. Email is normalized for comparison, display name is 1–100 nonblank characters, and password is 12–128 characters. Role is not accepted.
- `LoginRequest`: `email` and `password`.
- `TokenResponse`: `access_token`, `token_type: "Bearer"`, `expires_in`, and `account`.
- `EventCreate`: `title`, `description`, and `location`, each nonblank with documented maximum lengths.
- `EventPatch`: only `title`, `description`, and `location`; unknown fields are rejected.
- `SessionCreate`: `name`, `time_zone`, `registration_opens_at`, `registration_closes_at`, `starts_at`, `ends_at`, and positive integer `capacity`.
- `SessionPatch`: the same fields, subject to publication and participation-freeze rules; unknown fields are rejected.
- `CancelRequest`: nonblank `reason` from 1–500 characters for management cancellation; attendee self-cancellation has no free-text reason.
- `AdminAccountPatch`: optional `role` (`ATTENDEE` or `ORGANIZER` only), optional `disabled`, and required nonblank `reason`; at least one change is required.
- `ConfirmRequest`: exactly one `simulation_result` enum and no payment fields.
- Booking, waitlist join, decline, leave, and attendee cancellation have empty JSON bodies.

`SessionView` includes status, cancellation time and reason, times, IANA zone, capacity, `occupied_count`, `available_count`, `waitlist_count`, registration status, and `version`. `ParticipationView` includes IDs, state, queue position only while actively waitlisted, allocation and confirmation timestamps, absolute deadline, terminal reason, owning session summary, and session version. `RosterView` contains the categorized snapshot described in Section 8.3. Every write schema rejects unknown fields.

The contract tests freeze required fields, enum values, authorization, idempotency requirement, response status, and result code for every route. A generated-client diff is reviewed like a source change.

## 9. Authentication, authorization, and security

This section implements the proposal's Section 2.4 security direction—authenticated access, permission checks, rate limiting, and HTTPS—and the more specific account and security requirements in PRD FR-01 and NFR-03. The proposal does not prescribe JWT, refresh-token, or CSRF mechanics; Sections 9.1 and 9.2 define those implementation choices.

### 9.1 Passwords and tokens

- Hash passwords with Argon2id using a maintained password library and calibrated parameters.
- Issue a signed JWT access token with a 15-minute lifetime.
- Required claims are `iss`, `aud`, `sub`, `iat`, `nbf`, `exp`, and `jti`.
- Keep the access token in browser memory and send it as `Authorization: Bearer`.
- Generate a random 256-bit refresh token with a 7-day absolute lifetime.
- Store the refresh token only in a `Secure`, `HttpOnly`, `SameSite=Lax` cookie scoped to `/api/v1/auth`.
- Store only its SHA-256 digest in PostgreSQL and rotate it on every refresh.
- Keep signing keys in local secrets or Kubernetes Secrets, never in source control.

The signing algorithm and key size are pinned in configuration and cannot be selected from an incoming token header. Initial implementation uses EdDSA with an Ed25519 private signing key and public verification key.

### 9.2 CSRF and browser storage

Login sets a separate `Secure`, `SameSite=Lax`, non-HttpOnly CSRF cookie. Refresh and logout require the same value in `X-CSRF-Token`; the server compares its digest with the authentication session. The CSRF value remains stable for one refresh-token family and is cleared on logout or family revocation. State-changing business APIs use the bearer access token and do not authenticate from the refresh cookie.

The frontend must not put access or refresh tokens in `localStorage`. A page reload obtains a new access token through the cookie-protected refresh endpoint.

### 9.3 Current-account enforcement

JWT validation proves token integrity but does not replace current authorization checks. Every protected HTTP request loads the account and rejects an expired token, disabled account, or insufficient current role. Organizer ownership is checked against the target event.

Disabling an account:

1. Locks and marks the account disabled.
2. Revokes all refresh sessions.
3. Records a management action in the same commit.
4. Publishes a post-commit control invalidation when Redis is available.

The WebSocket gateway closes affected connections on that invalidation, checks account state before private delivery, and periodically revalidates connected accounts. Redis loss cannot grant protected HTTP access.

### 9.4 WebSocket connection tickets

Browsers cannot reliably add an `Authorization` header to a WebSocket upgrade. A client therefore calls `POST /realtime/tickets`, which is exempt from business-command idempotency.

- An anonymous request may obtain only public published-session scope.
- An authenticated request may obtain public, own-participation, and authorized management scopes.
- The API creates a random, single-use ticket valid for 30 seconds.
- Redis stores only a ticket digest with account ID when present, access-token expiry, and permitted scope.
- The browser offers `ticketbooking.v1` and `ticketbooking.ticket.<base64url-ticket>` through `Sec-WebSocket-Protocol`; the gateway selects `ticketbooking.v1`.
- The gateway validates the `Origin`, atomically consumes the ticket, and redacts the protocol header from logs.
- Ticket issuance or connection fails when Redis is unavailable; the UI falls back to public or authenticated REST polling.

This Redis dependency affects real-time convenience only. It does not block booking, confirmation, cancellation, or authoritative queries.

### 9.5 Additional controls

- Validate all inputs with explicit size and format bounds.
- Parameterize all SQL through SQLAlchemy.
- Restrict CORS to the deployed frontend origin.
- Set HSTS, content security policy, frame, MIME-sniffing, and referrer headers at the gateway or frontend.
- Redact passwords, cookies, authorization and WebSocket protocol headers, tokens, and unnecessary personal data from logs.
- Apply configurable `REGISTRATION_WRITE_LIMIT_PER_MINUTE=30` per account and `LOGIN_LIMIT_PER_MINUTE=10` per source IP by default.
- Trust forwarded client IP headers only from the DOKS gateway's configured proxy ranges. Normalize IPv4 and IPv6 addresses before limiting and ignore spoofed forwarded headers from other peers.
- During Redis loss, each API replica uses an in-process limiter with the same thresholds and reports degraded accuracy.

## 10. Real-time and frontend design

### 10.1 Message contract

WebSocket messages are small invalidation events:

```json
{
  "type": "session.changed",
  "event_id": "uuid",
  "session_id": "uuid",
  "version": 13,
  "reason": "participation_changed",
  "occurred_at": "2026-11-14T22:00:01Z"
}
```

Private event:

```json
{
  "type": "participation.changed",
  "session_id": "uuid",
  "participation_id": "uuid",
  "version": 13,
  "reason": "offer_created",
  "occurred_at": "2026-11-14T22:00:01Z"
}
```

Public messages contain no attendee identifiers. Private messages identify only records owned by the authenticated account and are refresh triggers; the browser fetches the authoritative resource through REST. Supported types are:

- `session.changed`
- `participation.changed`
- `management.changed`
- `auth.revoked`
- `system.resync_required`

Messages may be duplicated, dropped, or received out of order. A client ignores an event whose version is lower than the newest version it has applied. A version gap triggers an immediate REST refresh.

The remaining three invalidation types have these shapes.

Management event, sent on a management channel:

```json
{
  "type": "management.changed",
  "event_id": "uuid",
  "sessions": [
    {"session_id": "uuid", "version": 13}
  ],
  "reason": "roster_changed",
  "occurred_at": "2026-11-14T22:00:01Z"
}
```

`sessions` lists every session whose version the commit changed. It is empty when only event-level fields changed.

Account control event, sent to every connection of the affected account regardless of its subscriptions:

```json
{
  "type": "auth.revoked",
  "reason": "account_disabled",
  "occurred_at": "2026-11-14T22:00:01Z"
}
```

Gateway resynchronization event, created by the gateway and never published through Redis:

```json
{
  "type": "system.resync_required",
  "reason": "redis_reconnected",
  "occurred_at": "2026-11-14T22:00:01Z"
}
```

Every `reason` is one of the values below. A reason describes why the message was sent; it never carries the resulting state, which the client reads through REST. Section 10.5 maps each committed operation to its messages and reasons.

| Type | `reason` values |
| --- | --- |
| `session.changed` | `participation_changed`, `capacity_changed`, `session_published`, `session_cancelled`, `event_cancelled`, `registration_closed` |
| `participation.changed` | `hold_created`, `waitlist_joined`, `offer_created`, `confirmed`, `cancelled`, `expired` |
| `management.changed` | `roster_changed`, `session_updated`, `event_updated` |
| `auth.revoked` | `account_disabled` |
| `system.resync_required` | `redis_reconnected` |

A client treats an unknown `reason` on a known type as a plain refresh trigger and ignores an unknown `type`, so a later release can add values without breaking older browsers. `participation.changed` uses the six reasons above and exposes no terminal reason code such as `HOLD_EXPIRED`; that code is part of the REST resource.

The gateway also sends two control messages that are not invalidations and carry no version:

```json
{
  "type": "connection.ready",
  "connection_id": "uuid",
  "account_id": "uuid",
  "heartbeat_interval_seconds": 25
}
```

`account_id` is `null` on an anonymous connection.

```json
{
  "type": "subscription.ack",
  "request_id": "uuid",
  "accepted": ["session:uuid"],
  "rejected": [
    {"channel": "event:uuid:management", "code": "FORBIDDEN"}
  ]
}
```

`request_id` echoes the client's `subscribe` or `unsubscribe` message. A rejected channel carries `RESOURCE_NOT_FOUND` for an unknown or unpublished session, `FORBIDDEN` for a management channel the account does not currently own or administer, or `INVALID_CHANNEL` for a malformed name. These rejections leave the connection open. A request for a channel outside the scope of the connection's ticket, such as `me:participations` on an anonymous connection, closes the connection with `4403` as Section 10.2 describes.

### 10.2 Connection and subscription protocol

After upgrade, the gateway sends `connection.ready` with the connection ID, authenticated account ID when present, and heartbeat interval. Clients send:

```json
{"type":"subscribe","request_id":"uuid","channels":["session:uuid","me:participations"]}
```

The gateway replies with `subscription.ack` listing accepted and rejected channels. `unsubscribe` uses the same shape. Anonymous sockets may subscribe only to published `session:{id}` channels. Authenticated sockets may also use `me:participations`; the management channel `event:{event_id}:management` is accepted only after current ownership or administrator checks. A connection is limited to 50 public session channels and one private account scope.

The gateway sends a WebSocket ping every 25 seconds and closes a connection that does not pong within 10 seconds. It closes expired authentication with code `4401`, unauthorized scope with `4403`, policy or subscription-limit violations with `4408`, and transient server failure with `1011`. Clients reconnect with capped exponential backoff and recreate subscriptions from current page state.

Before private delivery, the gateway verifies that the account remains enabled and the access-token expiry has not passed. It revalidates an otherwise idle authenticated connection every 60 seconds. `auth.revoked` contains no session version and instructs immediate logout. `management.changed` carries each affected session ID and version. `system.resync_required` has a reason and no version; it forces immediate REST reconciliation. After reconnecting to Redis Pub/Sub, every gateway emits `system.resync_required` to all connected clients.

### 10.3 Publication

`realtime/publisher.py` runs only after the transaction has committed. Services never call it. The caller is the route handler or the worker loop, and it passes every `SessionChange` that one commit returned.

`SessionChange` is defined in `app/schemas/realtime.py` so a service can return it without importing `app.realtime`. It is an internal result, not a WebSocket message, and it has no contract fixture.

| Field | Meaning |
| --- | --- |
| `event_id` | Parent event. |
| `session_id` | Session whose version this commit advanced. |
| `version` | `event_sessions.version` after this commit. |
| `cause` | Operation that started the transaction. One of the closed values below. It is not sent on the wire. |
| `transitions` | Participation state changes written by this transaction. Empty when the session changed and no participation did. |

Each transition has `participation_id`, `account_id`, and `reason`. That `reason` is a `participation.changed` value from Section 10.1. The publisher copies it onto the private message and does not derive it from `cause`. Offers created in the same transaction, and waiters ended as ineligible, are further transitions on the same `SessionChange`.

`None`, or an empty list, means no session version changed. The caller does not call the publisher, and nothing is written to Redis.

| `cause` | `session.changed` reason | `management.changed` reason |
| --- | --- | --- |
| `booking_hold` | `participation_changed` | `roster_changed` |
| `waitlist_join` | `participation_changed` | `roster_changed` |
| `confirmation` | `participation_changed` | `roster_changed` |
| `attendee_release` | `participation_changed` | `roster_changed` |
| `admin_participation_cancel` | `participation_changed` | `roster_changed` |
| `expiration` | `participation_changed` | `roster_changed` |
| `promotion` | `participation_changed` | `roster_changed` |
| `registration_closed` | `registration_closed` | `roster_changed` |
| `capacity_changed` | `capacity_changed` | `session_updated` |
| `session_published` | `session_published` | `session_updated` |
| `event_published` | `session_published` | `event_updated` |
| `session_cancelled` | `session_cancelled` | `session_updated` |
| `event_cancelled` | `event_cancelled` | `event_updated` |

`attendee_release` covers cancel, decline, and leave. `promotion` is only the worker scan that promotes and does nothing else. Promotion inside another transaction keeps that transaction's `cause`. `event_published` and `event_cancelled` are the event-level transactions; each affected session is one `SessionChange`, and all of them share that `cause`.

From one call the publisher emits the messages already required by Section 10.5:

- One `session.changed` per `SessionChange`, carrying that element's `version` and the session reason of its `cause`.
- One `participation.changed` per transition, on that account's channel.
- One `management.changed` for the call. Its reason comes from `cause`, and its `sessions` list contains every element of the call. Every element of one call has the same `cause`.

WebSocket messages keep the field name `reason`. `cause` is only the publisher's input.

Two commits in Section 10.5 do not produce a `SessionChange`. After an event-field edit, the same publisher module sends `management.changed` with reason `event_updated` and an empty `sessions` list. After an account is disabled, it sends `auth.revoked`. Both run after commit.

Publication is best-effort and bounded by a short timeout. Failure increments a metric and is logged with the correlation ID. A failure does not stop the remaining messages of the same call. There is no database outbox in the baseline; 60-second reconciliation and reconnect reads close the notification gap.

### 10.4 Frontend state

The frontend uses React Router for page routing and TanStack Query for server-state caching. An OpenAPI-generated TypeScript client prevents handwritten request/response drift.

- Event list: browsing, search, open-registration filter, and pagination.
- Event details: sessions, availability, current participation, deadline, and context-sensitive primary action.
- My Registrations: active and historical attempts, with time-limited actions first.
- Management: event/session editing, publication, cancellation, rosters, and permitted administration.

The UI:

- Uses both text and color for status and supports keyboard operation.
- Displays the exact statement “Simulated payment; no actual charge” and collects no payment credentials.
- Explains a waitlist position as “Currently number N; a seat is not guaranteed.”
- Uses a confirmation dialog for attendee and administrative cancellation.
- Shows submission progress and disables duplicate clicks while preserving the operation key.
- Uses server time plus response receipt time to display approximate countdowns.
- Refreshes when a countdown reaches zero; it never releases or confirms a seat locally.
- Shows “Real-time connection temporarily unavailable” while disconnected.
- Polls relevant state every 10 seconds while disconnected.
- Stops fallback polling after reconnect but reconciles every 60 seconds while connected.
- Fetches authoritative state on page entry, reconnect, version gap, and uncertain command outcome.

### 10.5 Redis names and publication matrix

The API, the workers, and the gateway are separate processes that meet only through Redis names and message bodies. This section fixes both. A name or message that is not listed here must be added to this section in the same change that introduces it.

**Keys**

Every name starts with `ticketbooking:v1:`. A change that alters the meaning or format of a value uses a new version prefix, so old and new replicas never read each other's data during a rollout.

| Key | Redis type | Written by | Read by | Expiry | Value |
| --- | --- | --- | --- | --- | --- |
| `ticketbooking:v1:rl:write:{account_id}:{window}` | String counter | API | API | 120 seconds | Registration writes by that account in the window. |
| `ticketbooking:v1:rl:login:{ip}:{window}` | String counter | API | API | 120 seconds | Login attempts from that normalized address in the window. |
| `ticketbooking:v1:ws:ticket:{digest}` | String | API | WebSocket gateway | 30 seconds | JSON object: `account_id` (or `null`), `access_expires_at`, and `scopes`. |

- `{window}` is the Unix time in seconds divided by 60 and rounded down. Rate limiting is a fixed one-minute window: the API increments the counter, sets the expiry when the counter is new, and rejects the request when the count exceeds the configured limit. `Retry-After` is the number of seconds left in the window. A fixed window can admit up to twice the limit across a window boundary; this is accepted because the limit protects capacity and is not a correctness rule. The in-process fallback used during Redis loss applies the same windows.
- `{ip}` is the normalized address text from Section 9.5. Keys are built and never parsed, so the colons in an IPv6 address are harmless.
- `scopes` holds the values below. The API grants them from the caller's current account when it issues the ticket; the gateway enforces them as Section 10.2 describes.

  | Scope | Granted to | Allows channels |
  | --- | --- | --- |
  | `public` | Every caller, anonymous included | `session:{session_id}` |
  | `account` | An authenticated, enabled account | `me:participations` |
  | `management` | A current `ORGANIZER` or `ADMIN` | `event:{event_id}:management`, after the per-event ownership or administrator check on subscribe |

- `{digest}` is the lowercase hexadecimal SHA-256 digest of the ticket. The API writes the key only if it does not exist. The gateway reads and deletes it in one atomic command, which makes the ticket single-use across gateway replicas.

**Pub/Sub channels**

| Redis channel | Published by | Message types | Delivered to WebSocket subscribers of |
| --- | --- | --- | --- |
| `ticketbooking:v1:ps:session:{session_id}` | API, workers | `session.changed` | `session:{session_id}` |
| `ticketbooking:v1:ps:account:{account_id}` | API, workers | `participation.changed`, `auth.revoked` | `me:participations` on connections authenticated as that account. `auth.revoked` reaches all of that account's connections. |
| `ticketbooking:v1:ps:event:{event_id}:management` | API, workers | `management.changed` | `event:{event_id}:management` |

- The published body is the UTF-8 JSON message from Section 10.1 with no additional envelope. The gateway forwards it unchanged after the delivery checks in Section 10.2.
- The gateway routes by the Redis channel name and never by fields inside the body. The account that receives a private message is therefore fixed by the publisher's choice of channel.
- Each gateway replica holds one pattern subscription to `ticketbooking:v1:ps:*` and filters in memory. This removes the race between a browser subscribing and the gateway subscribing in Redis. Per-channel subscriptions replace it only if load tests show the fan-out cost matters.
- `connection.ready`, `subscription.ack`, and `system.resync_required` originate in the gateway and are never published in Redis.

**Publication matrix**

The process that commits a transaction publishes its messages after the commit, whether that process is the API or a worker. The input is the `SessionChange` list defined in Section 10.3. The reason depends on `cause` and on each transition, not on which process committed.

- Every committed transaction that increments a session's version publishes exactly one `session.changed` carrying the new version, with the reason of the operation that started the transaction. This is a superset of the public rule in Section 10.3: a confirmation changes neither availability nor queue count but still publishes, so that versions seen by a subscriber are contiguous and a gap always means a lost message.
- The same transaction publishes one `participation.changed` for each participation whose state it changed, on the owner's account channel.
- The same transaction publishes one `management.changed` on the event's management channel.

| Committed operation | `session.changed` | `participation.changed` (recipient) | `management.changed` |
| --- | --- | --- | --- |
| Direct booking creates a hold | `participation_changed` | `hold_created` (caller) | `roster_changed` |
| Waitlist join | `participation_changed` | `waitlist_joined` (caller) | `roster_changed` |
| Confirmation succeeds | `participation_changed` | `confirmed` (caller) | `roster_changed` |
| Simulated payment fails | None; no state or version changes | None | None |
| Attendee cancels, declines, or leaves | `participation_changed` | `cancelled` (owner) | `roster_changed` |
| Administrator cancels a participation | `participation_changed` | `cancelled` (owner) | `roster_changed` |
| Hold or offer expires | `participation_changed` | `expired` (owner) | `roster_changed` |
| Promotion inside any of these transactions | Covered by the transaction's one message | `offer_created` (each promoted account); `cancelled` (each waiter ended as ineligible) | Covered by the transaction's one message |
| Registration closes | `registration_closed` | `expired` (each hold or offer owner); `cancelled` (each waiter) | `roster_changed` |
| Capacity changes | `capacity_changed` | Promotion row applies after an increase | `session_updated` |
| Session is published | `session_published` | None | `session_updated` |
| Session is cancelled | `session_cancelled` | `cancelled` (each active owner) | `session_updated` |
| Event is cancelled | `event_cancelled`, one per affected session | `cancelled` (each active owner) | `event_updated`, listing every affected session |
| Event fields are edited | None; no session version changes | None | `event_updated` with empty `sessions` |
| Account is disabled | None | `auth.revoked` with `account_disabled` (that account) | None |

Publishing an event publishes its sessions in the same transaction, so it follows the "Session is published" row for each session and sends one `event_updated` that lists them.

A waitlist position changes whenever an earlier waiter leaves or is promoted. The system does not send a private message to every later waiter. A browser showing a `WAITLISTED` participation subscribes to that session's public channel and refetches its own participation on each `session.changed`. The position itself is returned only by the authenticated REST read, which satisfies FR-09.2.

Messages are published in the order public, private, then management. Each publication is independent: a failure is counted and logged as Section 10.3 describes and does not stop the remaining ones. A transaction that affects many accounts, such as an event cancellation, sends its private messages in one pipelined round trip.

**Single source in code**

Phase 1 implements this section once, and no other module spells out a Redis name or message body by hand:

- `app/core/redis_keys.py` holds one function per key and channel in the tables above.
- `app/schemas/realtime.py` holds one Pydantic model per message type, one enum per `reason` list in Section 10.1, and the `SessionChange` result from Section 10.3. Contract fixtures cover the WebSocket messages only.
- `tests/contract/fixtures/` holds one JSON example per message type. A contract test validates every fixture against its model and asserts the exact strings the name functions return.

## 11. Worker design and recovery

One worker process can run all scan types initially. Each scan has an independent loop, bounded batch size, metrics, cancellation handling, and backoff on dependency failure.

Scans run every second by default:

1. Due `HELD` and `OFFERED` rows.
2. Published sessions past registration close with unfinished unconfirmed attempts.
3. Open sessions that have both free capacity and waiters.
4. The capacity-invariant query used for alerting.

Each scan selects candidates in a short read transaction. For each candidate session, the worker opens a write transaction, locks the session scope and rechecks state against database time. All transitions in the transaction share one correlation ID and have a null actor. A changed session gets one version bump and one `SessionChange`. A stale candidate leaves the version unchanged. Workers rely on these state checks for idempotency and write no command receipt.

The expiry scan processes the oldest deadlines first. Under the session lock, it expires all due `HELD` and `OFFERED` rows. If any expired, it runs FIFO promotion in the same transaction. Promotion rechecks registration using fresh database time after each queue-head account lock.

At registration close, due holds and offers expire with their respective reasons, waiters are cancelled with `REGISTRATION_CLOSED`, and confirmed rows retain their seats. Closure issues no offers.

Concurrent workers serialize on the session lock. The implementation selects candidates without `SKIP LOCKED`. If metrics show contention, independent sessions may be distributed across workers while preserving FIFO order within each session.

On shutdown, workers stop taking candidates and finish or roll back active transactions within the Kubernetes termination grace period. On restart, all work is rediscovered from PostgreSQL; no in-process timer or Redis message is required.

Each scan loop sets `worker_last_scan_timestamp_seconds` when a pass finishes without a dependency failure, including a pass that found no candidates (Section 14). A dependency failure leaves the previous value in place. The worker serves this metric, and its health endpoints, on `WORKER_METRICS_PORT` (Sections 13.3 and 13.5).

## 12. Failure behavior

**Redis unavailable**

- PostgreSQL-backed reads and all registration writes continue.
- API replicas use approximate in-process rate limits.
- WebSocket ticket creation and live notifications are unavailable.
- Connected clients fall back to polling after disconnection; healthy but stale clients reconcile within 60 seconds.
- Workers continue scanning, expiring, and promoting.
- On Redis recovery, gateways force immediate client reconciliation before resuming normal invalidations.

**PostgreSQL unavailable**

- Business reads and writes return `503 DATABASE_UNAVAILABLE`.
- No component allocates from Redis or cached availability.
- WebSocket invalidations may continue briefly but clients cannot treat them as authoritative.
- Components reconnect with bounded exponential backoff and resynchronize after recovery.

**API response lost after commit**

- The receipt and business effect remain atomic.
- A retry with the same idempotency key replays the result.
- The client can query `/requests/{request_id}` or its current participation.

**Publisher crash after commit**

- The state remains valid.
- Other clients recover by reconnecting or periodic REST reconciliation.

**Worker or gateway restart**

- Workers rediscover persisted deadlines and queue state.
- Browsers reconnect and fetch current state.
- No business state is stored only in those processes.

**Database Pod restart**

- The StatefulSet remounts its PVC.
- Migrations are not rerun automatically by each API replica.
- Committed records and deadlines must remain unchanged.

Persistent volumes are not backups. Storage corruption, accidental deletion, region failure, and automatic database failover are accepted limitations.

## 13. Deployment design

### 13.1 Local development

The complete target Compose stack contains:

- `frontend`
- `api`
- `websocket`
- `worker`
- `postgres`
- `redis`
- `prometheus`
- `grafana`
- `postgres-exporter`
- `redis-exporter`

The migration command runs explicitly before application startup. Compose health checks distinguish liveness from dependency readiness. `.env.example` lists every variable in Section 13.5 without real credentials. Compose passes a local `.env` to the API and the worker, so demo values such as a short `OFFER_VALIDITY_SECONDS` reach both processes that create offers; the `DATABASE_URL` that Compose assembles still takes precedence.

### 13.2 Kubernetes

Kubernetes resources are organized as a reusable base plus minikube and DOKS overlays.

- Deployments: frontend, API, WebSocket gateway, workers, Prometheus, and Grafana.
- StatefulSets and PVCs: PostgreSQL and Redis, as required by the PRD.
- ClusterIP Services: every internal component.
- Gateway and HTTPRoutes: `/`, `/api/v1`, and `/ws/v1`.
- One migration Job per schema release. The deployment script or CI stage waits for Job completion, verifies `alembic current` equals the expected head, and only then applies dependent Deployments.
- ConfigMaps for non-secret configuration and Secrets for database credentials, token keys, and administrator bootstrap values.
- PodDisruptionBudgets for API and WebSocket replicas where cluster size permits.
- NetworkPolicies restrict PostgreSQL and Redis to required application and monitoring Pods.
- PostgreSQL and Redis exporters run with least-privilege credentials and internal-only scrape endpoints.

The DOKS cluster is VPC-native, uses Kubernetes 1.33 or later, and uses the managed `cilium` GatewayClass. PostgreSQL and Redis are never exposed publicly. HTTPS and WSS terminate at the managed gateway using the configured DNS name and certificate.

Gateway policy sets a 30-second timeout for routine REST requests. The WebSocket route allows at least a one-hour idle timeout; the 25-second heartbeat keeps healthy connections active. Backend and gateway timeout values are tested in minikube and DOKS because controller support and field names are version-specific.

### 13.3 Health and lifecycle

The API and the WebSocket gateway serve these paths on their HTTP port:

- `/health/live`: process event loop is responsive; it does not query dependencies.
- `/health/ready`: required startup and dependency checks pass for that component.
- `/metrics`: Prometheus endpoint, internal only.

The worker is `python -m app.workers`, not the FastAPI application. It serves the same three paths on `WORKER_METRICS_PORT` (Section 13.5). That port is cluster-internal. No Gateway route publishes it.

Worker Pod probes use that port:

| Probe | Path | Succeeds when | Does not check |
| --- | --- | --- | --- |
| Liveness | `/health/live` | The process answers. | PostgreSQL, Redis, and scan age. A stalled scan must not restart the Pod. |
| Readiness | `/health/ready` | PostgreSQL accepts a trivial query. | Redis. Section 12 keeps scanning and promotion running without Redis; publication failure stays a metric. |
| Startup | `/health/ready` | The same readiness check, so a slow first database connection is not a restart. | Redis and scan age. |

Prometheus scrapes each worker Pod at `WORKER_METRICS_PORT` and path `/metrics`. A worker that has stopped scanning stays Ready as long as the process and PostgreSQL are up. The gauge and alert in Section 14 report that condition. Liveness and readiness do not.

Readiness fails when PostgreSQL is unavailable for the API or for workers. Redis loss marks degraded status in metrics but does not remove API or worker Pods from service, because core writes and scans remain valid. The WebSocket gateway may fail readiness if it cannot consume Redis.

Worker Deployments use the probe table above. API and WebSocket Deployments probe the same paths on their own HTTP port: liveness hits `/health/live`, and readiness and startup hit `/health/ready`. All Deployments use rolling updates, bounded termination grace periods, and resource requests and limits. API and WebSocket Deployments start with 1–3 replicas and a 60% CPU HPA target, then are adjusted from measured behavior.

### 13.4 Connection budget

Maximum database connections are calculated before deployment:

```text
sum(replica_count * process_count * (pool_size + max_overflow))
+ migrations
+ monitoring
+ administrative_headroom
<= PostgreSQL max_connections
```

An initial small pool is preferred over one large pool per replica. HPA maximums cannot be raised without recalculating this budget. Actual values depend on the approved DOKS and PostgreSQL resources and must be recorded in the deployment overlay and experiment report.

### 13.5 Configuration

All processes read configuration from environment variables through `app/core/config.py`, which validates every value at startup and refuses to start on a missing or malformed one. `.env.example` lists exactly the variables below. Secrets come from a local `.env` file or a Kubernetes Secret and never from source control or a ConfigMap.

| Variable | Default | Used by | Meaning |
| --- | --- | --- | --- |
| `ENVIRONMENT` | `local` | All | Deployment name written to logs and metrics: `local`, `minikube`, or `doks`. |
| `LOG_LEVEL` | `INFO` | All | Minimum log level. |
| `DATABASE_URL` | None; secret | API, workers, gateway, migration | `postgresql+asyncpg` URL. Compose assembles it from `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`. |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | Set per overlay | API, workers, gateway | Connection pool bounds, derived from the budget in Section 13.4. |
| `REDIS_URL` | `redis://redis:6379/0` | API, workers, gateway | Redis connection. |
| `HOLD_VALIDITY_SECONDS` | `300` | API | Lifetime of a new direct hold (PRD FR-04). |
| `OFFER_VALIDITY_SECONDS` | `600` | API, workers | Lifetime of a new waitlist offer (PRD FR-07). |
| `REGISTRATION_WRITE_LIMIT_PER_MINUTE` | `30` | API | Registration writes per account per minute (PRD FR-10). |
| `LOGIN_LIMIT_PER_MINUTE` | `10` | API | Login attempts per source address per minute (PRD FR-10). |
| `TRUSTED_PROXY_CIDRS` | Empty | API, gateway | Proxy ranges whose forwarded client address is trusted (Section 9.5). Empty means the peer address is used. |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173` | API, gateway | Frontend origins allowed by CORS and by the WebSocket `Origin` check. |
| `JWT_PRIVATE_KEY` | None; secret | API | Ed25519 key that signs access tokens. |
| `JWT_PUBLIC_KEY` | None | API | Ed25519 key that verifies access tokens. |
| `JWT_ISSUER`, `JWT_AUDIENCE` | `ticketbooking`, `ticketbooking-api` | API | Required `iss` and `aud` claim values. |
| `ACCESS_TOKEN_TTL_SECONDS` | `900` | API | Access-token lifetime (Section 9.1). |
| `REFRESH_TOKEN_TTL_SECONDS` | `604800` | API | Absolute refresh-token lifetime (Section 9.1). |
| `WORKER_SCAN_INTERVAL_SECONDS` | `1` | Workers | Delay between scans (Section 11). |
| `WORKER_SCAN_BATCH_SIZE` | `100` | Workers | Candidates taken per scan. The default is a starting value to be adjusted from load tests. |
| `WORKER_METRICS_PORT` | `9100` | Workers | Port of the worker HTTP server for `/health/live`, `/health/ready`, and `/metrics` (Section 13.3). Not the API port. |
| `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD` | None; password is secret | Bootstrap command | First administrator account. The application processes do not read them. |

The gateway needs no signing key: it authenticates connections through Redis tickets, which carry the account ID and access-token expiry. It reads PostgreSQL only for the account-state and ownership checks in Section 10.2.

Values that this design fixes are code constants and are not configurable: the lock, statement, and idle-transaction timeouts in Section 7.1, the limit of 20 offers per transaction, the 30-second ticket lifetime, the 25-second heartbeat, and the event-list page size of 20. The 10-second polling and 60-second reconciliation intervals are constants in the frontend. Changing any of them is a design change.

As PRD Section 8 requires, the validity and rate-limit settings apply only to records and requests created after a process starts with the new value. They never rewrite a persisted deadline.

## 14. Observability

All services emit structured JSON logs with timestamp, level, service, environment, request or correlation ID, operation, session ID when applicable, outcome, duration, and error code. Logs exclude secrets and unnecessary personal information.

Minimum application metrics:

- `http_requests_total`
- `http_request_duration_seconds`
- `business_operations_total`
- `booking_outcomes_total`
- `confirmation_outcomes_total`
- `rate_limit_rejections_total`
- `worker_scan_duration_seconds`
- `worker_candidates_total`
- `worker_overdue_items`
- `worker_oldest_overdue_seconds`
- `worker_last_scan_timestamp_seconds`
- `waitlist_promotion_latency_seconds`
- `capacity_violations`
- `db_pool_connections`
- `redis_operation_failures_total`
- `realtime_publish_failures_total`
- `websocket_connections`
- `websocket_messages_total`
- `websocket_reconnects_total`

Labels use bounded values such as service, operation, outcome, and error code. Account, participation, event, session, request, and correlation IDs belong in logs, not metric labels.

`worker_last_scan_timestamp_seconds` is a gauge, one series per scan loop. Its label `scan` is `expiry`, `closure`, `promotion`, or `invariant`. The value is the Unix time of the last pass that finished without a dependency failure. `worker_oldest_overdue_seconds` measures due rows that are still waiting. It does not say whether the loop is still running.

For `waitlist_promotion_latency_seconds` with cause `expiration`, pair new offers in queue order with expired deadlines in ascending order. Record each offer's allocation time minus its paired deadline after commit. For cancellation-driven promotions, derive report latency from the release and offer history rows in `participation_changes` that share a correlation ID.

Grafana dashboards cover:

- API traffic, latency, unexpected errors, and business outcomes.
- Occupancy correctness, expiry backlog, promotion latency, and worker scan freshness.
- PostgreSQL connections, transaction latency, locks, and storage.
- Redis availability, memory, and operation errors.
- WebSocket connections, publication failures, and reconnects.
- Kubernetes replicas, CPU, memory, restarts, and HPA behavior.

Required alerts:

- Any `capacity_violations > 0`: immediate critical alert.
- Oldest overdue item above 30 seconds for 1 minute.
- A Ready worker whose `worker_last_scan_timestamp_seconds` series is missing, or older than 30 seconds, for 1 minute. The Pod is up and PostgreSQL is reachable, but that scan loop has stopped. A PostgreSQL outage is the readiness failure, not this alert.
- Unexpected `5xx` ratio above 5% for 5 minutes.

Alert delivery is tested with safe synthetic metrics or fault injection, never by deliberately overbooking.

For real-time latency evidence, the committed session version correlates the database change, post-commit publication, and browser render. The server records the first after-commit timestamp; the browser estimates server-clock offset from REST response timestamps and records the render timestamp. Reports state the clock-offset method and measure commit-to-render latency over at least 100 changes.

## 15. Testing strategy

Testing focuses on the risks that define the project:

- Unit and API tests cover state rules, validation, permissions, stable errors, and idempotent retries.
- PostgreSQL integration tests use independent connections to verify capacity, FIFO ordering, confirmation/expiration races, and competing workers.
- Frontend and WebSocket tests cover reconnects, missing or out-of-order messages, polling fallback, and stale-version rejection.
- Deployment tests verify migrations, persistent restarts, Redis/PostgreSQL outages, and Kubernetes rollouts.
- Load tests measure correctness, latency, worker backlog, database connections, and HPA behavior under the PRD's acceptance workload.

Participation factories seed the history needed for the next transition. When a test mixes factory-created waiters with a service-level join, set the session's `last_queue_seq` to the highest seeded queue number first. `make_waiting` leaves that counter unchanged.

Run the PostgreSQL-backed suite locally with:

```bash
docker run -d --rm --name booking-test-db -p 55432:5432 -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=booking_test postgres:18
TEST_DATABASE_URL=postgresql+asyncpg://test:test@127.0.0.1:55432/booking_test uv run pytest
```

The suite runs after a clean `uv sync` on Python 3.14, pinned in `.python-version`, with no local alias or shim. Tests build `Settings` without `.env`, so local demo values never change them. `ACCEPTANCE_ROUNDS` sets the number of PRD capacity rounds in `tests/concurrency/test_acceptance.py`: 1 by default, 10 for a recorded evidence run.

Detailed scenarios and target values remain defined in PRD Section 2 and the acceptance cases in `DATABASE_DESIGN.md`. Test reports record the environment and mark each result as passed, failed, or not executed.

## 16. Delivery sequence

### Phase 1: contracts and persistence

- Add backend dependencies, settings, structured errors, logging, metrics, and test infrastructure.
- Implement SQLAlchemy models including `auth_sessions`.
- Create and verify the first Alembic migration.
- Freeze OpenAPI envelopes, stable codes, and WebSocket event schemas.
- Implement the Redis names, message models, and contract fixtures listed at the end of Section 10.5, and the settings in Section 13.5.
- Extend Compose with explicit migration and component commands.

Exit condition: migrations apply to a clean database, schema invariants are tested, and contracts are reviewable.

### Phase 2: identity and management foundation

- Implement registration, login, refresh rotation, logout, account loading, RBAC, and bootstrap administration.
- Implement event and session CRUD, ownership, publication, capacity editing, and audit actions.
- Add authorization matrix and token security tests.

Exit condition: FR-01 through FR-03 management and access cases pass.

### Phase 3: booking and waitlist correctness

- Implement the common lock entry point and idempotency receipts.
- Implement booking, waitlist, confirmation, cancellation, capacity promotion, and history.
- Implement scanner workers for expiry, closure, promotion, and invariant checks.
- Complete PostgreSQL concurrency tests before adding real-time behavior.

Exit condition: FR-04 through FR-08 and FR-10 correctness tests pass with zero invariant violations.

### Phase 4: frontend and real-time recovery

- Build the four React page types and generated API client.
- Add Redis rate limiting, post-commit invalidation, WebSocket tickets, gateway subscriptions, polling, and reconciliation.
- Test dropped and out-of-order messages and gateway restarts.

Exit condition: FR-09 and FR-11 pass without making Redis authoritative.

### Phase 5: Kubernetes, monitoring, and evaluation

- Add minikube and DOKS resources, migration Job, Gateway API routes, TLS, Secrets, PVCs, probes, and network policies.
- Add Prometheus, Grafana, dashboards, alerts, and HPA.
- Run persistence, outage, load, scaling, and final demonstration scenarios.
- State in the final report that this release replaced the proposal's outbox/Redis Streams pipeline with database scanning and its ingress with the DOKS-managed Gateway API.

Exit condition: NFR-01 through NFR-03 and the PRD success metrics have recorded evidence.

## 17. Traceability summary

- FR-01 to FR-03: Sections 8, 9, and 16.
- FR-04 to FR-08: Sections 6, 7, 8, and 11.
- FR-09: Section 10.
- FR-10: Sections 7.5, 8, and 9.
- FR-11: Section 10.4.
- NFR-01: Sections 4, 11, 12, and 13.
- NFR-02: Sections 13.4, 14, and 15.
- NFR-03: Sections 6 through 9.

## 18. Open deployment inputs and accepted limitations

The architecture is implementable locally without these values, but cloud release still requires:

- Project lead, test executor, deployment maintainer, and implementation ownership.
- DigitalOcean budget and quota.
- DOKS node and volume sizes.
- Public domain and certificate arrangement.
- Final connection-pool values derived from the chosen resources.
- Actual acceptance and demonstration dates.

Accepted first-release limitations:

- One PostgreSQL instance is a single point of failure.
- PVCs provide persistence, not backup or disaster recovery.
- Redis Pub/Sub can lose notifications; clients reconcile through REST.
- An offer or hold cannot confirm at or after registration close.
- Seats released after registration close remain empty.
- Account disablement does not automatically cancel a confirmed booking.
- Linear throughput improvement from additional API replicas is not promised.
- Redis inventory pre-deduction remains an optional, isolated experiment and is not part of the baseline booking path.
- A capacity increase does not expire overdue attempts first, so it may offer fewer seats than are about to be freed. The expiry scan closes the gap within about a second.
- Promotions inside attendee and administrator commands record no live latency metric. `python -m app.cli.promotion_report` measures them offline from history.
- Decline and leave keep the `SESSION_STARTED` check although valid data cannot reach it: by the start, every hold and offer has expired and every waiter has been closed.

Any change to capacity guarantees, FIFO fairness, persistence semantics, real payment scope, or the authoritative role of PostgreSQL requires a PRD update and a reviewed design revision.
