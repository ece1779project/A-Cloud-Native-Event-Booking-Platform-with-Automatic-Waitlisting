# Performance Fallback Plan

Version: 1.0  
Date: 2026-11-14  
Status: Contingency plan; nothing in this document is part of the baseline

## 1. Purpose

The baseline design in [TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md) and [DATABASE_DESIGN.md](DATABASE_DESIGN.md) chooses correctness and simplicity over raw throughput. PostgreSQL is the only authority for seats, every write to a session waits for that session's row lock, and Redis is used only for rate limiting and notifications.

This document describes what to change if load tests miss the targets in PRD Section 2. It is organized as four tiers, ordered from cheapest to most invasive. Apply one tier, measure again, and stop as soon as the targets are met. Each tier names the new problems it introduces and how to handle them, because every gain in this plan is paid for with complexity.

Nothing here should be built in advance. The PRD asks the team to investigate the cause before changing anything (Section 2) and to explain the specific problem that any added mechanism solves (NFR-01.17). A tier is justified only by a measurement that points at it.

## 2. What does not change

Three rules hold in every tier. A change that breaks one of them is outside this plan and needs a PRD revision.

PostgreSQL remains the final authority for seats. Redis may help decide which requests are worth sending to the database, but it never decides that a booking succeeded (NFR-01.23).

Registration writes keep working when Redis is down (NFR-01.19). Anything added in front of the database must have a bypass that falls back to the baseline path.

Waitlist order stays strictly first-come, first-served. Several tempting optimizations weaken this, and they are listed in Section 9 as rejected.

## 3. Diagnose before changing anything

The acceptance workload is 50 requests per second with roughly 70% reads, and the target is a P95 of one second. A booking transaction under a row lock takes a few milliseconds, so one session can process a few hundred serialized writes per second. If the test fails at 50 requests per second, the row lock is unlikely to be the first cause. The metrics in Section 14 of the technical design are enough to tell the causes apart.

| What the test shows | Most likely cause | Where to look |
| --- | --- | --- |
| Read endpoints are slow | Every read queries PostgreSQL | Tier 1, read cache |
| Everything slows down whenever bookings happen | Each change tells every viewer to refetch at the same moment | Tier 1, notification coalescing |
| Pool exhaustion and bursts of 503 | Requests waiting for a lock each hold a database connection | Tier 1, admission cap |
| Login is slow and CPU is saturated | Argon2id is deliberately expensive | Tier 1, test method |
| Only writes to a popular session are slow | Queueing on that session's row lock | Tiers 2 to 4 |

Record the failing metric and the evidence for its cause before starting a tier. That record becomes the "before" half of the comparison in the final report.

## 4. Tier 1: remove waste without changing the design

These changes leave the data model and the locking rules untouched. They are worth doing first because they address the causes that are most likely at the target load.

### 4.1 Cache session reads

Most of the load is reads, and the most common read is a session's availability. Each API replica keeps the response for `GET /sessions/{session_id}` in memory for up to 500 milliseconds.

The new problem is stale data: a viewer may see a free seat that was taken a moment ago. This is already safe in the baseline. Every response carries the session version, the client discards anything older than what it displays, and a booking is decided by the database at the moment of the write. The worst outcome is that an attendee clicks "Book" and is told to join the waitlist instead.

Only public, per-session data is cached. Anything that depends on who is asking, such as a participation or a roster, is never cached.

### 4.2 Coalesce public notifications and carry the counts

In the baseline, every committed change publishes a `session.changed` message, and every browser viewing that session reacts by fetching the session again. A burst of bookings on a session with a thousand viewers therefore produces a burst of a thousand reads per booking. This is the most likely reason for the whole system slowing down during a rush.

Two changes fix it. First, the gateway forwards at most one `session.changed` per session every 500 milliseconds, always the one with the highest version. Second, the public message carries `occupied_count`, `available_count`, and `waitlist_count`, so a viewer can update the page without any request. These are public figures already returned by the session endpoint, and FR-09.7 requires a REST fetch only for private messages.

This changes one rule in Section 10.5 of the technical design. That section makes versions contiguous so that a gap always means a lost message. With coalescing, gaps are normal. The client rule becomes simpler: apply a public message if its version is higher than the displayed one, and ignore it otherwise. The 60-second reconciliation still covers a lost message.

The cache lifetime and the coalescing interval together must stay well inside the two-second visibility target in PRD Section 2, which is why both are set to 500 milliseconds.

### 4.3 Cap the number of requests waiting on one session

A request waiting for a session lock holds a pooled database connection for the whole wait. During a rush on one session, the waiters can take every connection and starve requests for all other sessions.

Each API replica limits how many requests may be inside the lock entry point for the same session at once. A request beyond the limit is rejected immediately with `503 SERVICE_UNAVAILABLE` and a `Retry-After` header. Because the rejection happens before execution, it does not consume the idempotency key (technical design, Section 7.5), and the frontend retries with the same key.

The cost is that some attendees must retry during a rush. That is better than the alternative, in which they wait five seconds for a lock timeout and receive the same 503 anyway. Start with a limit equal to half of the replica's connection pool and adjust it from measurements.

### 4.4 Shorten the time the lock is held

Throughput on one session is the inverse of how long each transaction holds its lock. Authentication, input validation, rate limiting, and the first idempotency lookup all run before the lock is requested. Inside the lock, the transaction only counts, inserts, appends history, and writes the receipt. Publication to Redis happens after commit and is not awaited by the response.

This introduces no new problem. It is a review of the implementation against the order already described in Section 7 of the technical design.

### 4.5 Keep password hashing out of the measurement

Argon2id is slow on purpose. A load test that logs in on every iteration measures the password hash and nothing else. The test script should log in once per virtual user and reuse the access token, and login throughput should be measured as a separate scenario. In the API, the hash runs in a worker thread so that it cannot block the event loop for other requests.

## 5. Tier 2: an occupancy counter with a conditional update

Apply this tier when writes to one session are slow and the profile shows time spent counting rows or in the number of statements executed under the lock.

### 5.1 The change

The baseline counts the occupied rows of a session on every booking. This tier stores the result instead. `event_sessions` gains two columns, `occupied_count` and `waiting_count`, and a booking becomes a single statement:

```sql
UPDATE event_sessions
   SET occupied_count = occupied_count + 1,
       version = version + 1
 WHERE id = :session_id
   AND occupied_count < capacity
   AND waiting_count = 0
RETURNING capacity, occupied_count, version;
```

If the statement updates one row, a seat was free and is now taken. If it updates no row, the session is full or has a queue, and the API returns `WAITLIST_REQUIRED`. The remaining checks, such as the registration window and the account status, run afterwards in the same transaction, and a failed check rolls the update back.

It is important to be clear about what this does and does not do. It does not remove the row lock. PostgreSQL locks a row when it updates it and holds that lock until the transaction ends, so requests for the same session still run one at a time. The gain is that locking, checking, and incrementing become one statement in place of a lock, a count, and a decision in application code. The lock is held for less time, and the cost no longer grows with the session's capacity.

### 5.2 New problem: the counter can drift from the rows

The counter and the participation rows are now two records of the same fact. Every operation that changes occupancy or the queue must update both: booking, joining or leaving the waitlist, cancellation, expiry, promotion, registration closure, and session or event cancellation. Missing one produces a session that is silently wrong.

Three measures contain this. All writes still go through the single lock entry point in `app/db/locks.py`, and the counter is updated only there, so there is one place to get right. The table gains a check constraint, `occupied_count <= capacity`, which makes the database itself refuse an oversold session; the baseline has no such constraint, so this is a real improvement. Finally, the worker's capacity-invariant scan compares each counter with the actual row count. A mismatch raises the same critical alert as a capacity violation, and the repair is always to overwrite the counter with the count, never the reverse.

### 5.3 New problem: a frequently updated row bloats

The session row is now rewritten on every booking. Set a lower fill factor on `event_sessions` so that PostgreSQL can perform these updates in place, and confirm in the load test that the table does not grow.

## 6. Tier 3: a Redis admission gate

Apply this tier when a rush on one session sends far more requests to the database than there are seats, and Tier 2 has not brought the queue down to an acceptable length.

### 6.1 The idea

When 5,000 people try to book 100 seats, 4,900 requests will be rejected. In the baseline, each of them waits in the database queue to learn that. The gate lets Redis reject most of them in well under a millisecond, so only requests with a realistic chance reach PostgreSQL.

Redis holds one small record per open session: an approximate number of seats available for direct booking, and the session version that number was computed from. A Lua script performs the check and the decrement as one atomic step and returns one of three answers:

- `ADMITTED`: a token was taken, and the request proceeds to the normal database transaction.
- `FULL`: no token is left, and the API returns `WAITLIST_REQUIRED` without touching the database.
- `UNKNOWN`: Redis has no record for this session, and the request proceeds to the database as in the baseline.

### 6.2 Why this is safe

The original proposal treated the Redis counter as the inventory. This plan treats it as a doorman. The database transaction behind the gate is unchanged and still decides every booking.

That framing means the gate can only be wrong in harmless directions. If it admits too many requests, the extra ones reach the database and are rejected there. If it admits too few, some attendees are told the session is full while a seat is free, which is a temporary inconvenience. Neither error can oversell a session. A disagreement between Redis and PostgreSQL costs performance or convenience, never correctness.

### 6.3 New problems and how each is handled

A token can leak. If a request is admitted and its database transaction then fails, or ends in any outcome other than a new hold, the token must be returned. The API returns it in the same code path that handles the outcome. If the process crashes before doing so, the token stays lost until reconciliation.

Reconciliation repairs every kind of drift. A worker periodically reads the true state of each open session from PostgreSQL and writes it to Redis. The value is zero when anyone is on the waitlist, and capacity minus occupied seats otherwise. This also covers seats released by cancellation, expiry, or a capacity increase whose update to Redis was lost.

Reconciliation can overwrite newer data with older data. The worker reads the database, and by the time it writes to Redis, bookings may have happened. To prevent this, the write is also a Lua script, and it applies the new value only when the session version it carries is at least as high as the one stored. Requests that were admitted but had not yet committed may receive their tokens back for a moment; the extra admissions are rejected by the database, as described above.

A false `FULL` can trap an attendee between two answers. The gate says the session is full, the attendee tries to join the waitlist, and the database answers `DIRECT_BOOKING_AVAILABLE` because a seat is in fact free. Whenever the API returns that code, it immediately rebuilds the gate record for the session from the database, so the attendee's next booking attempt is admitted.

Redis can fail. A circuit breaker around the gate treats a Redis error or timeout as `UNKNOWN` and sends the request to the database. This is exactly the baseline behavior, so the fallback is safe by construction. While the breaker is open, the per-account rate limit is tightened to protect the database from the unfiltered rush.

Redis can restart empty. A missing record already means `UNKNOWN`, so the system degrades to the baseline until reconciliation rebuilds the records.

### 6.4 What was dropped from the original proposal

The original proposal also had the Lua script reject users who already hold a booking, using a Redis set of active users per session. This plan leaves that out. If the set is not cleaned up promptly after a cancellation or expiry, it blocks an attendee who is entitled to book again, and that error is a wrong answer to the user, not a harmless one. Duplicate bookings are already prevented by the partial unique index and by idempotency keys.

### 6.5 Where the gate does not help

The gate only filters direct bookings while seats are being claimed. Once a session is full, attendees join the waitlist, and every join still needs the session lock to assign a queue number. If the bottleneck is waitlist joins, this tier does nothing, and Tier 4 is the relevant one.

The gate adds a key and two scripts to Redis. If this tier is adopted, add them to Section 10.5 of the technical design, which requires every Redis name to be listed there.

## 7. Tier 4: smoothing the burst and batching writes

These two changes are for a single session that still cannot keep up after Tier 3. Both are substantial pieces of work.

### 7.1 A waiting room at the entrance

The system does not have to process a burst at the speed it arrives. When registration opens for a popular session, the gateway admits requests at a fixed rate that the database is known to sustain. Everyone else sees a waiting page and is admitted in turn. Ticketing systems commonly work this way, because flattening a burst is far cheaper than building a system that can absorb its peak.

This needs a waiting page, a rule for who is admitted next, and a clear decision about fairness: an attendee's place is now determined by when they entered the waiting room. That is a product rule, so the PRD must be updated before this is built.

### 7.2 Batching bookings for one session

Each booking currently pays for its own lock acquisition and its own commit, and the commit, which waits for the disk, is the expensive part. A batching layer collects the booking requests for one session over a few milliseconds and processes them in a single transaction: one lock, one count, many inserts, one commit.

This is the most effective optimization for a single hot row, and also the most complex change in this plan. Each request in a batch must still receive its own result and its own idempotency receipt. One request's rejection must not fail the others. Arrival order within the batch must be preserved so that the first requests receive the seats.

## 8. Locking decisions

Three questions come up whenever this topic is discussed, and the answers are the same in every tier.

A distributed lock is not introduced. The limit on throughput is that writes to one session must happen one at a time. Replacing the PostgreSQL row lock with a Redis lock moves the queue to a different place without making it shorter. A Redis lock also expires on a timer, so a slow transaction can lose its lock while still running and a second process can enter; guarding against that requires further machinery. The only acceptable use of a Redis lock in this system is coordination where a failure is harmless, such as preventing several replicas from rebuilding the same cache entry at once.

The row lock stays. It is the final arbiter in every tier. From Tier 2 onward it is taken implicitly by the `UPDATE` statement instead of an explicit `SELECT ... FOR UPDATE`, but it is the same lock with the same effect.

Optimistic locking is not used for bookings. It works well when conflicts are rare, because it avoids waiting. Booking the last seats of a popular session is the case where conflicts are constant, and optimistic locking turns each conflict into a failed transaction and a retry, which performs worse than an orderly queue. It remains a good fit for low-contention edits, such as an organizer changing an event's description.

## 9. Changes that were considered and rejected

Turning off synchronous commit makes writes faster by acknowledging them before they reach the disk. A database crash could then lose bookings that attendees were told had succeeded. That breaks the persistence guarantee in PRD Section 2.

Accepting a booking and processing it later would let the API respond immediately and move the work to a queue. It changes the API contract and the frontend flow, since the attendee no longer learns the outcome from the response. The latency target is one second, which does not call for it.

Assigning waitlist numbers from a global database sequence would let joins skip the session lock. A transaction that receives a lower number can commit after one with a higher number, so a seat can be offered to a later arrival during that window. This weakens strict first-come, first-served ordering, which the PRD allows only by an explicit decision of the project lead.

## 10. Evaluation and documentation

Each tier is an experiment with a recorded result. Before applying a tier, run the acceptance workload and keep the numbers. After applying it, run the identical workload on identical resources. The report for each tier states the P95 latency for reads and writes, throughput, the rate of unexpected errors, and the peak number of database connections, before and after, together with the machine sizes and replica counts that PRD Section 2 requires.

Every run also executes the capacity test from PRD Section 2: a session of 20 seats, 100 concurrent clients, and 1,000 requests, with zero capacity violations and zero duplicate active bookings. A tier that improves latency and fails this test is rejected.

The proposal committed the team to comparing Redis pre-deduction with a database-only approach. Tiers 2 and 3 are that comparison, and their before-and-after numbers belong in the final report whether or not the tiers are kept.

Adopting a tier changes the design, so the design documents change with it:

| Tier | Documents to update |
| --- | --- |
| 1 | Technical design, Sections 10.1 and 10.5: counts in the public message, coalescing, and the relaxed version rule. Section 8.5 if the admission cap uses a new error code. |
| 2 | Database design: the two counter columns, the check constraint, and the fill factor. Technical design, Section 7: the conditional update in the lock entry point. |
| 3 | Technical design, Section 10.5: the gate key and scripts. Section 12: gate behavior during Redis loss. Section 18: remove pre-deduction from the list of excluded items. PRD Section 3: pre-deduction is no longer only an experiment. |
| 4 | PRD first, because the waiting room changes what attendees experience and how fairness is defined. |
