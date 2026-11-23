# Implementation Conversation Log

This file records the approximate scope completed in each implementation
conversation. It is a hand-off log rather than a replacement for
`TECHNICAL_DESIGN.md`; behavior and contracts must still follow that document.

## 2026-11-17 — Yifeng scope and real-time foundation

### Request

- Identify Yifeng's responsibilities from the current design.
- Plan the work and begin with WebSocket support.

### Completed

- Split Yifeng's ownership into real-time delivery, frontend, cloud
  infrastructure, observability, autoscaling, and performance evaluation.
- Confirmed that the current baseline uses post-commit Redis Pub/Sub, not the
  proposal's older transactional-outbox/Redis-Streams wording.
- Added all server-to-client WebSocket message models, reason enums, client
  subscription requests, ticket scopes, and ticket responses.
- Centralized Redis keys, Pub/Sub channels, and Pub/Sub channel parsing.
- Added seven JSON contract fixtures and exact-name contract tests.
- Implemented secure random connection-ticket issuance, SHA-256 digest
  storage, 30-second expiry, and atomic single-use `GETDEL` consumption.

### Verification

- Relevant unit and contract tests passed.
- Ruff and IDE diagnostics reported no errors.

## 2026-11-17 — WebSocket gateway, fan-out, and publisher

### Request

- Continue implementing the WebSocket path.

### Completed

- Added `/ws/v1` upgrade handling with exact Origin validation and the
  `ticketbooking.v1` plus `ticketbooking.ticket.*` subprotocol contract.
- Added connection registration, `connection.ready`, subscription state,
  subscribe/unsubscribe acknowledgements, canonical channel parsing, ticket
  scope enforcement, and the 50-public-channel limit.
- Added published-session checks and current organizer/administrator checks
  for management subscriptions.
- Added authentication-expiry closure, 60-second account revalidation, and
  private-delivery account checks.
- Added one Redis pattern subscription per gateway replica, in-memory
  fan-out, account-wide `auth.revoked`, and `system.resync_required` after a
  Redis reconnect.
- Added gateway liveness, readiness, and Prometheus metrics endpoints.
- Added post-commit publication for public, private, management, event-update,
  and account-revocation invalidations.
- Preserved public → private → management publication order and pipelined
  multi-account private messages.
- Connected worker-produced `SessionChange` values to the real-time publisher.
- Added WebSocket metrics and publication-failure metrics.

### Verification

- 38 unit and contract tests passed.
- Ruff and IDE diagnostics reported no errors.

### Remaining dependency

- `POST /api/v1/realtime/tickets` cannot yet issue authenticated tickets
  correctly because access-token verification/current-account dependencies
  are still skeletons.
- API command routes are also skeletons, so their post-commit publication
  call sites must be connected when those routes are implemented.

## 2026-11-17 — Worker health and metrics service

### Request

- Continue implementation and begin maintaining a per-conversation log.

### Completed

- Added the worker's independent HTTP server on `WORKER_METRICS_PORT`.
- Added `/health/live`, `/health/ready`, and `/metrics`.
- Readiness checks PostgreSQL only. Redis availability and scan freshness do
  not change worker readiness, matching the technical design.
- Integrated the server into worker startup, signal handling, bounded
  shutdown, and resource cleanup.
- Added the WebSocket gateway to Compose with the fixed 25-second ping and
  10-second pong timeout.
- Added worker port exposure and API, gateway, and worker readiness checks to
  Compose.
- Expanded `.env.example` and shared Compose application settings for the
  current configuration model.
- Added this implementation conversation log.

### Verification

- Worker health and runner tests passed.
- Ruff reported no errors for the worker implementation.
- `docker compose config --quiet` accepted the resulting Compose model.

## 2026-11-17 — API metrics, local monitoring, and Kubernetes base

### Request

- Continue completing the remaining coding work one item at a time.

### Completed

- Added the API process `/metrics` endpoint and isolated endpoint tests.
- Added Prometheus scrape configuration for API, WebSocket, worker,
  PostgreSQL exporter, and Redis exporter.
- Added required correctness, backlog, scan-freshness, missing-series, and
  HTTP error-ratio alert rules.
- Added Grafana Prometheus datasource provisioning.
- Added Prometheus, Grafana, PostgreSQL exporter, and Redis exporter services
  to Compose with persistent monitoring volumes.
- Added Kubernetes base resources for namespace, shared configuration,
  PostgreSQL and Redis StatefulSets/PVCs, migration Job, API, WebSocket, and
  worker Deployments and Services.
- Added a minikube overlay using a locally built image and NodePorts for API
  and WebSocket access.
- Added a DOKS overlay with DigitalOcean block storage, Cilium Gateway API
  HTTPS/WSS routes, HTTP-to-HTTPS redirect, API/WebSocket HPAs, disruption
  budgets, and PostgreSQL/Redis ingress policies.

### Verification

- 43 unit and contract tests passed; Ruff passed.
- Compose configuration validation passed.
- Kubernetes base, minikube overlay, and DOKS overlay rendered successfully
  with `kubectl kustomize`.

### Environment-limited checks

- Prometheus `promtool` container validation could not run because the local
  Docker daemon was not running.
- Kubernetes server-side/client discovery validation could not run because no
  cluster context was available. Rendering itself succeeded.

## 2026-11-18 — HTTP metrics, dashboards, and Kubernetes monitoring

### Request

- Continue with the remaining implementation work.

### Completed

- Added API middleware for safe `X-Request-ID` propagation, bounded
  route-template HTTP metrics, latency histograms, and structured access-log
  fields.
- Added a provisioned Grafana overview dashboard for capacity correctness,
  overdue work, WebSocket connections/messages, publication failures, API
  traffic/latency/errors, dependency availability, and worker scan age.
- Added Kubernetes Prometheus, Grafana, PostgreSQL exporter, and Redis
  exporter workloads, Services, PVCs, generated ConfigMaps, probes, and
  resource bounds.
- Extended DOKS data-store NetworkPolicies for the exporters.
- Removed the migration Job from normal Kustomization application so
  deployments cannot race schema migration.
- Added a deployment runbook covering Secret creation, minikube/DOKS
  prerequisites, explicit migration ordering, rollout checks, and monitoring
  validation.

### Verification

- HTTP middleware tests and Ruff checks passed.
- Grafana JSON and provisioning YAML parsed successfully.
- Compose configuration and all three Kubernetes variants rendered
  successfully after monitoring was included.

## 2026-11-18 — Frontend and real-time browser foundation

### Request

- Continue implementing the remaining work.

### Completed

- Added a React/Vite/TypeScript frontend with React Router, TanStack Query,
  and an OpenAPI-generated typed client.
- Added event list, event detail, My Registrations, and management route
  foundations with disconnected 10-second polling.
- Added browser WebSocket ticket acquisition, subprotocol connection,
  ticket-scope-aware subscriptions, capped reconnect backoff, 60-second
  reconciliation, version-gap detection, stale-message rejection, and query
  invalidation.
- Added the required disconnected-state message and waitlist-position
  wording.
- Added a production Nginx image with SPA fallback, API/WebSocket proxying,
  one-hour WebSocket timeout, and baseline security headers.
- Added frontend services/images and routing to Compose, Kubernetes base,
  minikube, and DOKS.

### Verification

- OpenAPI TypeScript generation and the production frontend build passed.
- Frontend version-tracking tests passed.
- `npm audit --omit=dev` reported zero production dependency
  vulnerabilities.
- Compose and all Kubernetes variants still rendered successfully.

### Deferred

- Authentication forms and command actions depend on the unfinished backend
  authentication and route implementations.
- The development toolchain audit reports a transitive `picomatch` ReDoS
  advisory through Vite/Vitest. npm offers only a forced breaking downgrade;
  production dependencies are unaffected, so no forced change was applied.

## 2026-11-18 — Frontend authentication and attendee commands

### Request

- Continue implementation.

### Completed

- Added in-memory access-token state, cookie/CSRF refresh rotation, early
  refresh scheduling, StrictMode-safe refresh de-duplication, login, logout,
  and account UI.
- WebSocket connections now restart when authentication changes, so a new
  ticket receives the correct scopes.
- Added context-sensitive direct booking/waitlist actions and active
  participation actions for confirmation, cancellation, offer decline, and
  waitlist leave.
- Added exact simulated-payment and waitlist-position disclosures plus
  confirmation dialogs for destructive attendee actions.
- Each command keeps one idempotency key across an uncertain retry and checks
  `GET /requests/{request_id}` after a network failure.

### Verification

- OpenAPI generation and the production frontend build passed after the
  authentication and command work.

### Deferred

- Runtime end-to-end verification waits for backend authentication and API
  route implementations.
- Organizer and administrator command UI remains pending those routes.

## 2026-11-18 — Organizer and administrator interface

### Request

- Continue implementation.

### Completed

- Added role-gated manageable-event listing and event management routes.
- Added event creation and event-field editing.
- Added session creation with explicit IANA time zone and offset-bearing
  timestamp conversion.
- Added event publication, event/session cancellation with required reasons
  and irreversible-action confirmation, and capacity editing.
- Added roster counts and attendee rows for held, offered, confirmed, and
  waitlisted states.
- Added management-channel subscriptions and corrected browser subscription
  synchronization so channels removed during navigation are explicitly
  unsubscribed.
- Management writes preserve an idempotency key until a successful response.

### Verification

- OpenAPI generation and the production frontend build passed.

### Deferred

- Runtime authorization and command behavior still require the corresponding
  backend route implementations.

## 2026-11-18 — Discovery, administration, deadlines, and load tests

### Request

- Continue implementation.

### Completed

- Added event keyword search, open-registration filtering, and forward/back
  cursor pagination.
- Added an administrator-only account page with cursor pagination, attendee
  and organizer role changes, disable/enable controls, required audit reasons,
  and protected administrator/self rows.
- Added server-clock calibration from API response metadata.
- Added approximate hold/offer countdowns and automatic authoritative query
  refresh when a deadline is reached.
- Added a Locust harness for the PRD's approximately 7:3 mixed API traffic,
  50-request/second shape, 100-user booking contention, 1,000 commands,
  idempotent replays, and expected business-rejection classification.
- Added dataset, execution, correctness-check, and evidence-retention
  instructions for ten-round concurrency and five-minute performance runs.

### Verification

- Four frontend unit tests passed.
- OpenAPI generation, TypeScript compilation, and production frontend build
  passed.
- Ruff accepted the load scenario and Locust discovered `BookingUser`.

### Deferred

- Runtime account administration and deadline reconciliation require the
  backend routes and a running integrated stack.
- Performance numbers cannot be collected until backend routes, disposable
  accounts, and an integrated target environment are available.

## 2026-11-18 — Structured logging and redaction

### Request

- Continue implementation.

### Completed

- Added one-line JSON logging with UTC timestamp, level, service,
  environment, logger, message, request/correlation context, and bounded
  operational fields.
- Added request-ID context propagation from HTTP middleware into nested log
  calls.
- Added field allowlisting and redaction for bearer credentials, WebSocket
  tickets, passwords, cookies, access/refresh/CSRF tokens, authorization, and
  WebSocket protocol headers.
- Replaced Worker text logging and configured API, WebSocket, Worker, and
  Uvicorn loggers through the shared formatter.
- Added database-pool gauges for open and checked-out connections using
  SQLAlchemy pool events.
- Added bounded Redis failure counters for ticket issue/consume, Pub/Sub
  receive, publication, and readiness ping operations.

### Verification

- Structured-field, request-context, and redaction tests passed.
- Redis ticket-failure metric tests passed.
- Ruff accepted all logging changes.

## 2026-11-19 — Main/auth integration assessment

### Request

- Pull the latest `main`, explain Yifeng-side complexity, summarize completed
  and remaining work, and identify Jianye's remaining work.

### Completed

- Fast-forwarded local `main` by five commits to `33925a0`, bringing in
  authentication, account administration, rate limiting, bootstrap-admin,
  error handling, and their tests.
- Preserved all local work through a full untracked-file stash and merged the
  four overlapping files: API middleware, Redis names, API entry point, and
  `uv.lock`.
- Combined auth exception/rate-limit startup with request metrics,
  structured logging, metrics routing, and real-time Redis names.

### Verification

- No merge conflicts remain.
- 69 unit/contract tests passed and one timing test skipped.
- One OpenAPI contract test still fails because participation routes remain
  unimplemented; it is not a pull/merge failure.
- The incoming auth changes contain pre-existing Ruff findings that should be
  cleaned with their owning modules.

## 2026-11-19 — Minimal authenticated realtime integration

### Request

- Keep the implementation simple, prioritize core behavior, finish remaining
  Yifeng work, and defer integration/load testing.

### Completed

- Added a small shared access context exposing the verified account and JWT
  expiry without duplicating token verification.
- Implemented anonymous and authenticated `POST /realtime/tickets`.
- Granted only `public` to anonymous callers, added `account` for
  authenticated callers, and added `management` only for organizers/admins.
- Mapped Redis ticket failures to the existing retryable service-unavailable
  error.
- Published `auth.revoked` after a committed account disable.
- Added HSTS to the existing frontend security headers. API/browser traffic
  remains same-origin through the frontend/Gateway proxy, so no additional
  cross-origin layer was introduced.

### Verification

- 61 unit tests passed and one unrelated timing test remains skipped.
- Ticket API unit tests cover anonymous and organizer scope grants.
- Frontend tests and production build passed against the pulled OpenAPI.

### Deferred

- Session/participation/event Publisher call sites wait for those API routes
  to exist.
- Integration, end-to-end, failure, and pressure tests are intentionally
  deferred for manual staged review.

## 2026-11-20 — Participation implementation pull

### Request

- Pull the latest `main` and determine whether participation work landed.

### Completed

- Fast-forwarded `main` by three commits to `13bcd8c` without conflicts.
- Pulled booking/waitlist, confirm/cancel, administrator cancellation,
  participation queries, idempotency, event/session management, timing
  helpers, and post-commit publication call sites.
- Regenerated the frontend OpenAPI client against the latest contract.

### Verification

- 75 unit and contract tests passed.
- The production frontend build passed.

### Remaining participation work

- Attendee `decline` and `leave` service and route implementations are still
  absent and remain listed in `JIANYE_DEV_PLAN.md`.
- Database-backed integration/concurrency suites were not run in this
  lightweight pull check.

## 2026-11-20 — First local Compose smoke test

### Request

- Begin testing the local changes after Docker became available.

### Completed

- Created an ignored local `.env` with disposable PostgreSQL credentials and
  an Ed25519 JWT key pair.
- Built and started PostgreSQL, Redis, migration, API, WebSocket gateway,
  Worker, and frontend through Compose.
- Removed an unused frontend testing dependency and regenerated the npm
  lockfile so Docker `npm ci` is reproducible.
- Changed the runtime dependency to `uvicorn[standard]`; the base Uvicorn
  install had no WebSocket protocol implementation and returned HTTP 404 for
  upgrades.
- Changed Nginx upstreams to use Docker's runtime DNS resolver. Static
  startup resolution retained stale API/WebSocket container IPs after a
  rebuild and sent `/api` traffic to the gateway.

### Verification

- All seven core containers became healthy.
- API, gateway, and Worker readiness checks passed.
- Frontend CSP and HSTS headers were present.
- Local account registration/login passed.
- Anonymous Ticket scope was `public`; authenticated attendee scope was
  `public,account`.
- Anonymous WebSocket upgrade returned `connection.ready`.
- Authenticated WebSocket upgrade exposed the account ID and accepted
  `me:participations`.
- Frontend-origin event, login, Ticket, and WebSocket proxy requests passed
  after dynamic DNS resolution was enabled.

## Log maintenance

At the end of each implementation conversation:

1. Add one dated section with the request, completed work, and verification.
2. Record dependencies or intentionally deferred work.
3. Do not describe planned work as completed.
4. Keep detailed API behavior in the technical design and tests; keep this
   file concise enough to scan during hand-off.
