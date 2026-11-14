# Product Requirements Document: A Cloud-Native Event Booking Platform with Automatic Waitlisting

Version: 1.2 | Date: 2026-11-14 | Scope: First complete release of the course project

This document is based on Sections 1, 2, and 4 of [Project_Proposal.md](Project_Proposal.md) and the [system architecture diagram](planned-system-architecture.svg). It does not adopt the schedule, team assignments, or integration plan in Section 3. The proposal establishes the product direction and technical constraints; this document adds business rules, default parameters, and acceptance criteria. The repository currently contains the planning documents and a local Docker Compose scaffold with a health endpoint. The requirements below remain to be implemented and verified; they do not represent existing functionality or measured results.

**Reading conventions.** Every numbered rule is mandatory unless it says "optional." "May" grants a permission to a user or implementer; it never marks a rule the system is free to skip. Tests and reviews cite rules by number, for example FR-04.3.

**Differences from the proposal.** This document replaces the transactional outbox and Redis Streams pipeline from Section 3.3 of the proposal with database scanning (NFR-01), and uses the DOKS-managed Gateway API where the architecture diagram shows an ingress. The final report must state both changes.

**Changes in 1.2.** Rules are numbered individually. Deployment and observability requirements are renumbered NFR-01 and NFR-02, security becomes NFR-03, and the frontend pages become FR-11. Registration writes now continue during a Redis outage. Connected clients reconcile state every 60 seconds, and sessions carry a version for discarding stale updates. Section 3 gains a terminology table, and Section 8 gains a parameter table and three open engineering items.

## 1. Product Positioning and Problem

This project addresses registration scenarios with limited capacity, such as university workshops, course activities, and conference sessions. When registration opens, multiple attendees may compete for the last available seat. Later, cancellations and unconfirmed holds release seats. Manually maintaining a waitlist makes ordering difficult to guarantee and can leave seats unused for extended periods. Without reliable booking status, attendees must repeatedly contact organizers, increasing the communication burden for both sides.

The platform should connect “browse events—reserve a temporary seat—confirm registration—cancel or expire—automatically promote from the waitlist” into a complete workflow. Attendees should know whether they have a seat, when they must confirm it, and how many people are ahead of them on the waitlist. Organizers should be able to configure capacity and review registrations without contacting waitlisted attendees individually. Administrators handle necessary user and event management.

The project focuses on backend correctness under concurrent requests, asynchronous tasks, multiple application replicas, and service restarts. A lightweight frontend supports actual operations and displays status, without pursuing elaborate visual design. At this stage, rules and acceptance criteria are established so that APIs, background tasks, real-time notifications, and cloud deployment share the same business semantics.

## 2. Product Goals and Success Criteria

The primary goals are to prevent overbooking and duplicate seat allocation, and to assign released seats according to a defined first-come, first-served rule. Secondary goals are to automate expiration handling, waitlist offers, and recovery, reducing manual intervention. The value of cloud-native capabilities should be demonstrated through reproducible deployment, concurrency, and recovery experiments rather than treating the use of a particular technology as a success criterion.

No operational data is currently available. The baseline for every metric is “to be established in the first test.” The values below are proposed acceptance targets for this release, not production SLAs or commitments about existing performance.

| Metric | Acceptance Target and Measurement | Acceptance Stage |
| --- | --- | --- |
| Capacity and duplicate bookings | For a session with capacity 20, use 100 concurrent clients to submit a total of 1,000 booking requests, covering duplicate submissions, cancellations, confirmations, and expiration races. Run 10 rounds, with zero capacity violations and zero duplicate active bookings for the same user. Use multiple test accounts and count rate-limit rejections separately so that rate limiting does not conceal concurrency defects. | After the core workflow is complete, then verify again in the cloud |
| Waitlist fairness | With a fixed enqueue order, cover one available seat, multiple seats, multiple workers, withdrawal by the first person in line, and offer expiration. Every allocation selects the earliest eligible attendee at that moment, with no queue jumping, duplicate seat-holding offers, or unallocated available seats. | Waitlist workflow acceptance |
| Reclamation and promotion latency | While the system is healthy, registration is open, and eligible waitlisted attendees exist, P95 ≤ 5 seconds from the hold deadline or successful cancellation commit to persistence of a waitlist offer. Record at least 100 observations for each category, including maximum latency and test load. | Integration and cloud acceptance |
| Status visibility | With a healthy WebSocket connection, P95 ≤ 2 seconds from transaction commit to display of the new state. Synchronize authoritative state within 5 seconds of reconnection. Record at least 100 state changes using client and server timestamps. | Frontend/backend integration |
| Basic API performance | With fixed cloud resources and replica counts, warm up the system, then test for 5 minutes at a total of 50 requests/second with an approximately 7:3 read/write ratio. P95 ≤ 1 second for both ordinary queries and business writes; unexpected 5xx responses < 1%. Count expected business rejections separately; the target load should not continuously trigger 429 responses. | Cloud performance acceptance |
| Persistence and recovery | Restart the API, workers, WebSocket gateway, and database Pod separately. Committed bookings, waitlist ordering, and original deadlines must remain intact. Process the test backlog within 30 seconds after dependencies become healthy, without allocating seats twice. | Failure recovery acceptance |
| Scaling correctness | In an environment with spare resources, trigger HPA scale-out and scale-in for the API and WebSocket gateway. Record replica counts, latency, throughput, and database connections. Capacity rules must hold throughout, and disconnected clients must be able to recover their state. | Final acceptance |

All performance reports must record machine specifications, resource limits, replica counts, dataset size, request mix, and measurement methods. If initial tests miss a target, investigate the cause first. Any target adjustment must have its rationale recorded by the project team; changing metrics must not substitute for fixing correctness defects.

## 3. User Scenarios and Scope

| User | What They Need to Accomplish | Priority |
| --- | --- | --- |
| Attendee | Browse events and sessions, obtain a temporary seat, and confirm registration through simulated payment. | P0 |
| Attendee | Explicitly join the waitlist when full, view their position, and accept or decline a time-limited offer. | P0 |
| Attendee | Review and cancel their bookings, clearly distinguishing pending confirmation, waitlisted, and confirmed states. | P0 |
| Organizer | Create and publish events, configure session capacity and registration times, and review registrations and waitlists for their events. | P0 |
| Administrator | Manage account permissions and problematic events while retaining traceable management records. | P0 |
| Attendee and organizer | See seat availability and status changes without refreshing, and still retrieve correct results when the connection is disrupted. | P1 |
| Project maintainer | Start, deploy, and observe the system consistently, and verify behavior under peak load and restarts. | P0 for basic deployment; P1 for scaling evaluation |

P0 requirements are prerequisites for a complete business workflow. P1 requirements are implemented after P0 is stable, but remain within the scope of this complete release. Development may proceed in stages; priority must not be interpreted as automatically dropping real-time updates or scaling verification.

This release includes standard registration and login, role-based access control, event and session management, one-seat-per-person booking, temporary holds, simulated payment confirmation, cancellation, automatic expiration, FIFO waitlisting, real-time status updates, containerization, persistence, monitoring, and cloud deployment. An event may contain multiple sessions, each with independent capacity accounting and a separate queue.

This release excludes real payments, refunds, seat selection, ticket-type pricing, group purchases, transfers, check-in or ticket validation, complex event recommendations, and native mobile applications. It does not check scheduling conflicts across sessions. Email and SMS notifications, waitlist priorities, an additional payment step after accepting a waitlist offer, multi-region disaster recovery, and automatic database failover are deferred for future evaluation and are not current acceptance conditions.

Redis inventory pre-deduction is an optional performance experiment only. PostgreSQL guarantees inventory correctness in the baseline implementation. Additional cache-consistency complexity must not be introduced into the core booking path without a controlled comparison.

### Terminology

| Term | Meaning in this document and the database design |
| --- | --- |
| Session | One scheduled occurrence of an event, with its own capacity and queue. Database sessions and login sessions are always named in full. |
| Participation (attempt) | One account's record for one session, from the first hold or waitlist entry to its final outcome. |
| Hold | A participation in `HELD`: a seat reserved directly, awaiting confirmation. |
| Offer | A participation in `OFFERED`: a seat reserved for a waitlisted attendee, awaiting confirmation. |
| Booking | A participation that holds a seat directly or is confirmed (`HELD` or `CONFIRMED`). |
| Registration | The overall activity of signing up, and the time window in which a session accepts it. |
| Cancel, decline, leave | The attendee actions that end a hold or confirmed booking, an offer, and a waitlist entry, respectively. All three end in `CANCELLED`. |
| Expire | The system ends a hold or offer whose deadline has passed. |
| Promote | The system turns the earliest eligible waitlist entry into an offer. |

## 4. Functional Requirements and Business Rules

### 4.1 Accounts, Events, and Access Control

**FR-01: Identity and permissions.**

- **FR-01.1** Unauthenticated users may browse published events.
- **FR-01.2** Booking, confirmation, cancellation, and waitlist operations require login.
- **FR-01.3** Public registration creates attendee accounts only; administrators grant organizer privileges.
- **FR-01.4** The initial administrator is created during deployment initialization, with no public administrator registration.
- **FR-01.5** Organizers may manage events they create and may also register as attendees.
- **FR-01.6** Administrators may manage all events.
- **FR-01.7** The server must check both role and resource ownership rather than relying on hidden frontend buttons.
- **FR-01.8** Attendees may access only their own bookings, waitlist details, and private notifications.

Acceptance testing must access the same resources as an anonymous user, two attendees, two organizers, and an administrator, checking both allowed and denied paths and directly modifying resource IDs to test unauthorized access. Return 401 for unauthenticated requests and 403 for unauthorized operations. Return 404 for requests naming another user's private records, so that the response does not reveal whether those records exist.

**FR-02: Event and session management.**

- **FR-02.1** An event must contain at least a title, description, location, and organizer.
- **FR-02.2** A session contains a name, start and end times, capacity, registration opening time, and registration deadline.
- **FR-02.3** Capacity must be a positive integer.
- **FR-02.4** Registration must open before its deadline; the deadline must be no later than the session start, which must precede its end.
- **FR-02.5** Support draft, published, and cancelled states.
- **FR-02.6** Drafts are visible only to their organizer and administrators; publication requires at least one valid session.
- **FR-02.7** Registration status is determined by server time, independently of whether scheduled tasks execute on time.
- **FR-02.8** After publication, descriptions and locations may be edited and capacity may be increased.
- **FR-02.9** Capacity may also be reduced, but not below the number of currently occupied seats.
- **FR-02.10** Once any registration or waitlist record exists, this release no longer allows changes to the session's registration or event times, avoiding changes to previously promised deadlines.
- **FR-02.11** If rescheduling is necessary, cancel the original session and create a new one.
- **FR-02.12** Existing waitlisted attendees retain priority when capacity increases.
- **FR-02.13** Events and sessions with existing records must not be physically deleted.

Acceptance covers invalid times and capacities, draft access, changes by an organizer who does not own the event, capacity reduction concurrent with booking, and waitlist promotion after a capacity increase. Seats and queues for different sessions must not affect one another.

**FR-03: Browsing and queries.**

- **FR-03.1** Sort the event list by the nearest session start time.
- **FR-03.2** Support keyword search and an “open for registration” filter, with 20 entries per page by default.
- **FR-03.3** Details show session times, time zone, capacity, currently available seats, waitlist count, and registration status.
- **FR-03.4** Attendees may query their current status, confirmation deadline, and waitlist position for each session.
- **FR-03.5** Organizers may view counts and corresponding lists of confirmed bookings, temporary holds, seats held by waitlist offers, and waitlisted attendees for their sessions.
- **FR-03.6** Lists expose only account identifiers, names, contact details, and statuses necessary for management, and are not public to ordinary attendees.
- **FR-03.7** Displayed availability is a snapshot at query time; the booking transaction determines the final outcome.

Acceptance covers full sessions, registration not yet open, closed registration, cancelled sessions, pagination, and query permissions. Counts and details must agree for the same database state.

### 4.2 Booking, Confirmation, and Waitlisting

**FR-04: Booking and temporary holds.**

- **FR-04.1** An account may have at most one active participation record per session, in the held, waitlisted, offered, or confirmed state.
- **FR-04.2** When seats are available and no waitlist exists, a successful booking creates a temporary hold valid for 5 minutes by default.
- **FR-04.3** When the session is full or waitlisted attendees are awaiting allocation, new arrivals must not bypass the queue to take an available seat.
- **FR-04.4** The API must explicitly indicate that waitlisting is required; the user chooses whether to join and is not automatically enqueued.
- **FR-04.5** The hold period starts when allocation is successfully persisted.
- **FR-04.6** Its effective deadline is the earlier of “allocation time plus validity period” and “registration deadline.”
- **FR-04.7** Return server time and the absolute deadline.
- **FR-04.8** If less than 5 minutes remain, display the actual window.
- **FR-04.9** The API may report a successful hold only after the transaction commits.

Acceptance covers competition for the last seat, repeated clicks, multiple browser tabs, an existing waitlist with seats not yet allocated, and booking requests before registration opens or after it closes.

**FR-05: Simulated payment and confirmation.**

- **FR-05.1** Both held and offered records are confirmed through the same simulated payment action.
- **FR-05.2** A simulated success immediately changes the record to confirmed, without another pending-payment stage or a second capacity deduction.
- **FR-05.3** A simulated failure preserves the original state and deadline, allowing retries within the validity window.
- **FR-05.4** The page must clearly state “Simulated payment; no actual charge” and must not collect real payment information such as bank card details.
- **FR-05.5** Confirmation is valid only if the server determines that the record has not expired when checking the competing state transitions.
- **FR-05.6** Reject confirmation when `current time >= deadline`, even if the expiration worker has not run.
- **FR-05.7** A confirmation/expiration race must produce only one valid outcome; the seat cannot be both confirmed and released.

Acceptance covers success, retry after failure, repeated successful requests, boundaries immediately before and after the deadline, and confirmation concurrent with reclamation.

**FR-06: Joining and leaving the waitlist.**

- **FR-06.1** Enqueueing is allowed only while registration is open, the session is not cancelled, and waitlisting is necessary.
- **FR-06.2** If a seat is already available and no other attendee is waiting, return a result indicating that direct booking is possible and guide the user to book, without silently creating a hold.
- **FR-06.3** Each valid enqueue operation receives a persistent, unique sequence number within the session.
- **FR-06.4** FIFO follows the enqueue order serialized by the server, not browser click times or client clocks.
- **FR-06.5** Repeated enqueue requests must not create additional records or change ordering.
- **FR-06.6** Positions start at 1 and reflect the ordering of currently active waitlisted records.
- **FR-06.7** Attendees who have received a time-limited offer no longer occupy a waitlist position.
- **FR-06.8** Users may leave and rejoin at the tail.
- **FR-06.9** Users whose previous booking was cancelled or whose offer expired may also reapply while registration is open, without inheriting their former priority.

Acceptance covers concurrent joins, duplicate joins, withdrawal, rejoining, and dynamic position updates.

**FR-07: Automatic promotion and expiration reclamation.**

- **FR-07.1** When a confirmed booking is cancelled, a temporary hold expires, a waitlist offer is declined or expires, or capacity increases, the system automatically allocates available seats to eligible waitlisted attendees.
- **FR-07.2** Offers are valid for 10 minutes by default and must also end no later than the registration deadline.
- **FR-07.3** An offer occupies a seat as soon as it is created.
- **FR-07.4** Whether the user is online or successfully receives a push notification does not affect its timer.
- **FR-07.5** The authoritative offer record must be persisted first and retrievable through queries.
- **FR-07.6** Each available seat may correspond to only one active offer.
- **FR-07.7** Allocate multiple seats to different users in FIFO order.
- **FR-07.8** Multiple concurrent workers must not skip the head of the queue, allocate duplicates, or overbook.
- **FR-07.9** Attendees who have withdrawn, become ineligible, or had their accounts disabled must be skipped with the reason recorded.
- **FR-07.10** Expired offers do not automatically rejoin the queue; their released seats pass to subsequent attendees.
- **FR-07.11** After restarting, workers must catch up on expired tasks from persisted state rather than relying on in-process timers surviving.
- **FR-07.12** At the registration deadline, stop accepting bookings, waitlist joins, and confirmations, and stop issuing new offers.
- **FR-07.13** Unconfirmed holds expire; remaining waitlist entries end with the reason “Registration closed”; confirmed bookings remain valid.
- **FR-07.14** A seat released after the registration deadline therefore stays empty until the session starts.
- **FR-07.15** This is an accepted limitation of the single-deadline rule in this release, and the final report must list it.

Acceptance covers successive attendees failing to confirm, offers issued near the registration deadline, recovery after background tasks are paused, duplicate execution by multiple workers, and backlog remaining after registration closes.

**FR-08: Cancellation and administrative operations.**

- **FR-08.1** Before a session starts, attendees may cancel held or confirmed bookings, leave the waitlist, or decline offers.
- **FR-08.2** After the session starts, they may only view historical status.
- **FR-08.3** Cancelling a confirmed booking does not involve a real refund.
- **FR-08.4** Repeated cancellation requests must preserve the same outcome and release the seat only once.
- **FR-08.5** Organizers may cancel their own events or sessions; administrators may cancel any event or session.
- **FR-08.6** Cancellation must immediately prevent new business operations.
- **FR-08.7** Existing bookings and waitlist entries display “Event cancelled,” pending offers become invalid, and further promotion stops.
- **FR-08.8** Preserve the original history and cancellation reason.
- **FR-08.9** Background tasks running concurrently with cancellation must obey the same rule.
- **FR-08.10** Administrators may disable accounts and change organizer permissions, but must not use administrative operations to bypass the queue or capacity limits.
- **FR-08.11** Disabled accounts cannot perform business writes or subscribe to private updates.
- **FR-08.12** Their unconfirmed holds expire normally, their waitlist entries become ineligible for promotion, and their confirmed bookings remain unless explicitly cancelled by an administrator when necessary.

Acceptance covers repeated cancellation, cancellation concurrent with confirmation and promotion, whole-event cancellation, account disabling, and permission changes. Administrative operations must record the actor, target, time, and reason so that state changes can be explained.

### 4.3 States, Real-Time Feedback, and Error Handling

Records may be presented using the following product states. Database table design is an implementation decision, but these meanings must remain unchanged.

| State | Occupies a Seat? | Main Permitted Transitions |
| --- | --- | --- |
| Held `HELD` | Yes | Confirmed after simulated payment succeeds; cancelled voluntarily; expired at the deadline |
| Waitlisted `WAITLISTED` | No | Offered when a seat becomes available; withdrawn voluntarily; ended when registration closes or the event is cancelled |
| Offered `OFFERED` | Yes | Confirmed after simulated payment succeeds; declined voluntarily; expired at the deadline |
| Confirmed `CONFIRMED` | Yes | Cancelled voluntarily before the start; cancelled administratively |
| Cancelled `CANCELLED` | No | Terminal state; retain reasons such as withdrawal, registration closure, or event cancellation |
| Expired `EXPIRED` | No | Terminal state; retain the original deadline |

Every allocation must satisfy: **held count + offered count + confirmed count ≤ session capacity**. An expired record that has not yet been reclaimed cannot be confirmed, but its seat must be released atomically before being allocated again. Seats must not be reused early based solely on cached values or a displayed timer. Terminal records cannot be revived. Re-registration creates a new participation attempt, and retries of old requests must not affect the new record. Confirmed records remain after an event ends. The page labels them “Ended” based on session time, without requiring an additional set of check-in states.

**FR-09: Real-time updates and recovery.**

- **FR-09.1** Changes to session availability and waitlist counts are pushed to users viewing that session.
- **FR-09.2** Personal booking status, waitlist position, and offers are pushed only to the relevant user; management lists are visible only to authorized users.
- **FR-09.3** All pushes must occur after database transaction commit.
- **FR-09.4** Duplicate or out-of-order pushes must not move the page from a newer state back to an older one.
- **FR-09.5** Each session carries a version that increases with every committed change to its seats, queue, capacity, or status.
- **FR-09.6** REST responses and pushes include that version, and the client ignores any response or push older than the state it already displays.
- **FR-09.7** Private pushes are refresh triggers: the client fetches authoritative state through REST instead of trusting the pushed payload.
- **FR-09.8** On initial page entry, WebSocket reconnection, or an uncertain operation outcome, the client retrieves current state through REST.
- **FR-09.9** When disconnected, display “Real-time connection temporarily unavailable,” reconnect automatically, and poll the state needed by the current page every 10 seconds.
- **FR-09.10** Once connected, stop the 10-second fallback polling but keep a reconciliation query every 60 seconds, because a push can be lost while the connection stays healthy.
- **FR-09.11** Frontend countdowns are for display only; refresh state when they reach zero rather than independently declaring confirmation success or releasing a seat.

Acceptance covers pushes lost while disconnected and while connected, duplicate and out-of-order messages, offers issued while the client is offline, gateway restarts, and attempts to subscribe to another user's private state.

**FR-10: Duplicate requests, rate limiting, and failure feedback.**

- **FR-10.1** Writes such as booking, confirmation, cancellation, and waitlist operations must have idempotent semantics.
- **FR-10.2** Client retries of the same operation reuse its request identifier.
- **FR-10.3** Reject the same identifier with different business parameters.
- **FR-10.4** Database constraints must prevent duplicate active records even when different request identifiers arrive concurrently.
- **FR-10.5** Retain idempotency records for at least 24 hours; state transitions and task consumption must continue preventing duplicate side effects over the long term.
- **FR-10.6** A timeout must not immediately display “Booking failed” and encourage a second booking.
- **FR-10.7** Instead, display “Checking result” and query the original request or current participation record.
- **FR-10.8** APIs provide stable error codes and readable messages distinguishing full capacity, closed registration, expiration, insufficient permission, invalid parameters, rate limiting, and temporary service unavailability.
- **FR-10.9** By default, registration-related writes are limited to 30 per account per minute, and login attempts to 10 per source IP per minute.
- **FR-10.10** Thresholds are configurable.
- **FR-10.11** Ordinary queries and WebSocket keepalives do not count toward the registration-write quota.
- **FR-10.12** Rate limiting returns 429 with retry guidance.

Acceptance covers successful commits with lost responses, retries across replicas, replaying an old cancellation request after re-registration, duplicate queue-task execution, and recovery after rate limiting. Service unavailability must not produce a false business success response.

**FR-11: Frontend pages.**

- **FR-11.1** The frontend uses four lightweight page types: event list, event details, “My Registrations,” and management pages.
- **FR-11.2** Depending on current state, the details page shows primary actions such as “Book,” “Join Waitlist,” “Simulate Payment and Confirm,” or “Cancel.”
- **FR-11.3** Explain waitlist position as “Currently number N; a seat is not guaranteed.”
- **FR-11.4** “My Registrations” prioritizes records requiring time-limited confirmation.
- **FR-11.5** All writes show submission progress and temporarily prevent repeated clicks.
- **FR-11.6** Cancellations and administrative cancellations use simple confirmation dialogs.
- **FR-11.7** Pages should work on desktop and mobile browsers, communicate state using both text and color, and support basic keyboard operation.

## 5. Technical Constraints and Nonfunctional Requirements

**NFR-01: Deployment, data, and recovery.**

- **NFR-01.1** Retain FastAPI, PostgreSQL, Redis, separate background workers, and the WebSocket gateway from the proposal.
- **NFR-01.2** Use Docker and Docker Compose to start the complete local stack, with uv and its lockfile pinning Python dependencies.
- **NFR-01.3** Validate Kubernetes configuration in minikube before deploying to DigitalOcean Kubernetes.
- **NFR-01.4** PostgreSQL and Redis use StatefulSets, PVCs, and corresponding persistent storage.
- **NFR-01.5** Stateless components use Deployments, Services, and health probes, with external access over HTTPS/WSS.
- **NFR-01.6** Do not expose PostgreSQL or Redis directly to the public internet.
- **NFR-01.7** Inject credentials through Kubernetes Secrets or local environment configuration rather than committing them to the repository.

**Public entry point.**

- **NFR-01.8** Use the [DOKS-managed Gateway API](https://docs.digitalocean.com/products/kubernetes/how-to/use-gateway-api/) on a VPC-native cluster running Kubernetes 1.33 or later.
- **NFR-01.9** DigitalOcean manages the Cilium gateway controller and provisions a network load balancer when the team creates a `Gateway` with the `cilium` GatewayClass.
- **NFR-01.10** The deployment maintainer owns the public DNS record, HTTPS certificate configuration, and `HTTPRoute` rules that send REST requests to FastAPI and WebSocket connections to the WebSocket service.
- **NFR-01.11** Configure timeouts so that routine HTTP requests have a deadline and healthy WebSocket connections can remain open between updates.
- **NFR-01.12** PostgreSQL is the sole source of truth for users, events, sessions, participation records, waitlist ordering, and deadlines.
- **NFR-01.13** Redis supports rate limiting, Pub/Sub, and other auxiliary functions.
- **NFR-01.14** Losing cached data or notifications must not lose booking facts.
- **NFR-01.15** Background reclamation and promotion must be recoverable from the database; critical tasks must not depend solely on Redis Pub/Sub.
- **NFR-01.16** Database scanning is the baseline mechanism for reliable processing.
- **NFR-01.17** If an outbox, Redis Streams, or a delay queue is introduced, explain the particular problem it solves rather than treating every exploratory proposal as mandatory architecture.
- **NFR-01.18** When Redis is unavailable, database-backed state queries remain available and pages fall back to polling.
- **NFR-01.19** Registration-related writes continue, because their correctness depends only on PostgreSQL and a rejected confirmation would cost an attendee a seat they already hold.
- **NFR-01.20** Each API replica falls back to an in-process limiter with the same per-account and per-IP thresholds, so the effective limit is approximate while Redis is down.
- **NFR-01.21** Background processes continue reclamation and promotion under database rules.
- **NFR-01.22** When PostgreSQL is unavailable, reject business writes and clearly indicate temporary unavailability.
- **NFR-01.23** Do not fall back to allocating seats independently in Redis.
- **NFR-01.24** Resynchronize state after recovery.
- **NFR-01.25** Store database times in UTC and display an explicit time zone.
- **NFR-01.26** Confirmation and expiration checks must use a valid current database time after acquiring the relevant concurrency controls, rather than a stale time obtained before waiting for a lock.

Acceptance includes startup from a clean environment following the documentation, database migrations, cloud deployment, rolling restarts, and recovery from dependency outages. Verify that records and deadlines remain unchanged after persistent volumes are remounted, and that a temporary database outage produces no false successes. A single PostgreSQL instance is an accepted availability limitation for this release. Persistent volumes are not backups; this release does not promise data recovery from disk corruption, accidental deletion, or a region-wide outage.

**NFR-02: Observability and scaling.**

- **NFR-02.1** Use Prometheus, Grafana, and DigitalOcean Monitoring to observe services and resources.
- **NFR-02.2** At minimum, expose request volume, latency, error rate, booking and confirmation outcomes, expiration-processing backlog, age of the oldest overdue task, waitlist promotion latency, capacity violations, database connections, Redis memory, WebSocket connections, and replica counts.
- **NFR-02.3** Logs include a request or correlation identifier, session, and operation outcome, while avoiding passwords, complete tokens, and unnecessary personal information.
- **NFR-02.4** Alert immediately on capacity violations.
- **NFR-02.5** Also alert when the oldest overdue expiration task remains more than 30 seconds overdue for 1 minute, or when unexpected 5xx responses exceed 5% in a 5-minute window.
- **NFR-02.6** Verify the alerting path using safe test metrics or fault injection, without deliberately causing production overbooking.
- **NFR-02.7** Configure HPA for the API and WebSocket gateway.
- **NFR-02.8** Initial validation may use 1–3 replicas and a 60% CPU target; adjust actual settings to available cloud resources and document them.
- **NFR-02.9** The total database connection-pool budget must account for maximum replica counts and worker connections, leaving administrative headroom.
- **NFR-02.10** More application replicas do not necessarily increase database throughput.

Acceptance requires reproducible load-test scripts, dashboards, evidence of triggered alerts, and scaling experiment records. Clients may reconnect when the WebSocket gateway scales in, but persisted business state must not be lost. Linear scaling is not promised, and capacity validation must not be removed to improve throughput.

**NFR-03: Security and implementation latitude.**

- **NFR-03.1** Security requirements include hashed password storage, keeping credentials out of the client, auditing administrative operations, and denying continued access to protected resources after authentication tokens expire or accounts are disabled.
- **NFR-03.2** Validate user input on the server.
- **NFR-03.3** All persisted modifications use database transactions and necessary constraints.
- **NFR-03.4** Specific locking strategies, table structures, and API routes are left to technical design, which must demonstrate compliance with the business rules in Section 4.

## 6. Dependencies, Risks, and Mitigation

The responsibilities below describe required functions, without assigning individuals or adopting the proposal's original team assignments. The project team determines actual owners during execution. External resources have not yet been verified.

| Dependency or Risk | Delivery Impact and Response | Follow-up Role |
| --- | --- | --- |
| DigitalOcean account, quotas, budget, domain, and TLS | Determine whether DOKS, persistent volumes, and a public demonstration can be delivered. Validate a minimal deployment and certificates early. Confirm the budget ceiling before purchasing resources, while maintaining a fully runnable local environment. | Deployment maintainer; project lead confirms the budget |
| Inconsistent understanding of states and interfaces | The frontend may show success after the backend record has expired. Fix states, error codes, deadline semantics, and message contracts before implementation; use shared state examples during integration. | Backend and frontend implementers jointly |
| Concurrency races, task retries, and multiple replicas | May cause duplicate holds, releases, or promotions. Ensure correctness through transactions, constraints, and idempotent handling, focusing on the last seat and confirmation/expiration races. | Backend implementer; test executor reviews |
| Successful data commit but lost notification | Users may miss time-limited confirmation. Persist offer state in the database and compensate through reconnection queries and fallback polling. Clearly state that countdowns continue while offline. | Real-time communication and frontend implementers |
| Single-database failure and connection exhaustion | Application scaling may amplify pressure. Limit connection budgets, monitor backlog and connection counts, and verify restart recovery. Do not claim database high availability. | Backend implementer and deployment maintainer |
| Scope or schedule pressure | First ensure correctness of capacity, waitlisting, and persistence, then complete real-time and scaling evaluation. Optional performance experiments and visual polish may be reduced first. Any removal of committed functionality requires the project lead's confirmation and a scope update. | Project lead |

## 7. Delivery Checkpoints and Acceptance Approach

These checkpoints follow dependency order without assigning people, durations, or fixed dates. The team schedules actual work according to current progress.

| Checkpoint | Deliverables and Passing Conditions |
| --- | --- |
| Business rules and environment ready | State and interface contracts agreed; Compose starts successfully; a minimal cloud service connects to a persistent database, with data retained after a database Pod restart. |
| Complete core workflow | Normal and exceptional scenarios for FR-01–FR-08 and FR-10 pass. Reproducible tests cover concurrent holds, confirmation/expiration races, and FIFO promotion. |
| Demonstrable cloud release | FR-09, FR-11, and NFR-01 pass. Through the public hostname, verify that an HTTPS booking request reaches FastAPI and that the browser receives a private waitlist update over WSS. Demonstrate oversubscribed registration, cancellation, promotion, confirmation, and reconnection recovery. |
| Final quality acceptance | NFR-02, NFR-03, and Section 2 metrics are verified. Deliver deployment instructions, test results, failure and scaling experiments, known limitations, and a demonstration procedure. |

The acceptance scenarios following each FR define the minimum scope of its tests. Implementers submit evidence, the team's test executor reviews it, and the project lead confirms scope and user workflows. Existing team members may hold multiple roles. Prefer automated integration tests for backend rules, browser-based integration checks for real-time interactions, and deployment and failure experiments for cloud capabilities. The final report must distinguish “Passed,” “Failed,” and “Not yet executed”; untested behavior cannot be counted as passed.

For the demonstration, use a session with capacity 2: two attendees obtain holds, and a third and fourth join the waitlist. The first confirms, the second cancels the hold, and the third receives an offer and confirms. Then cancel a confirmed booking; the fourth receives an offer but does not confirm in time, and the system automatically reclaims the seat. Show waitlist positions, capacity statistics, and real-time updates throughout, and verify after a worker restart that original deadlines remain valid. Shorter validity periods may be configured to shorten the demonstration, provided the report documents that configuration and separately verifies the production defaults of 5 and 10 minutes.

## 8. Default Decisions and Outstanding Items

This document adopts one seat per person per session, 5-minute holds, 10-minute waitlist offers, strict FIFO, offers that occupy seats, immediate confirmation after simulated payment success, and confirmation windows that never extend beyond the registration deadline. These additional product decisions keep implementation simple and behavior consistent and may be used directly as development requirements. Validity periods and rate-limit thresholds are managed through environment configuration, without adding complex organizer settings pages. Parameter changes apply only to new records and do not retroactively alter persisted deadlines.

| Parameter | Default | Defined in |
| --- | --- | --- |
| Hold validity | 5 minutes, capped at the registration deadline | FR-04 |
| Offer validity | 10 minutes, capped at the registration deadline | FR-07 |
| Registration-write rate limit | 30 per account per minute | FR-10 |
| Login rate limit | 10 per source IP per minute | FR-10 |
| Idempotency record retention | At least 24 hours | FR-10 |
| Event list page size | 20 | FR-03 |
| Polling while disconnected | Every 10 seconds | FR-09 |
| Reconciliation while connected | Every 60 seconds | FR-09 |
| Worker scan interval | Every second | Database design, Section 5 |
| HPA starting point | 1–3 replicas, 60% CPU target | NFR-02 |
| Overdue-task alert | Oldest task more than 30 seconds overdue for 1 minute | NFR-02 |
| Error-rate alert | Unexpected 5xx above 5% in a 5-minute window | NFR-02 |

Three engineering items are still open and block parallel feature work. Each needs a named owner before implementation starts.

| Open item | What must be decided |
| --- | --- |
| API and message contract | REST routes, request and response schemas, the full error-code table, and WebSocket message formats. The first checkpoint in Section 7 depends on it. |
| Authentication scheme | Token type and lifetime, how a disabled account loses access at once, and how the WebSocket handshake authenticates. |
| Role assignment | Which team member acts as project lead, test executor, deployment maintainer, and each implementer named in Section 6. |

No product questions currently block local development. Before launch, the project lead must establish the cloud budget, target deployment specifications, and actual acceptance date; the deployment maintainer must arrange the domain and TLS. These details cannot be inferred from the proposal. Whether to adopt Redis inventory pre-deduction will be decided after the backend implementer provides a comparison between the database baseline and the experimental approach; it is not a prerequisite for completing the first release.

If future requirements involve real payments, purchasing paid cloud resources, changing waitlist fairness rules, or reducing capacity and persistence guarantees, the project lead must decide first and update this document. Implementers may independently determine ordinary page layouts, error wording, and implementation details that do not change business semantics.
