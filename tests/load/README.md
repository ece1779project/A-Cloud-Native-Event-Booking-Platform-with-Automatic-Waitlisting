# Load and scaling tests

These tests produce evidence for the PRD targets; they are not ordinary CI
tests. Run them against an isolated environment containing disposable load
accounts and a published target session.

## Dataset

Create at least 100 enabled attendee accounts whose emails follow one
template, for example `load{index}@example.com`, all with the same
load-test-only password. Create one published event/session with:

- capacity 20 for the concurrency acceptance run;
- open registration throughout the run;
- at least 100 distinct eligible accounts;
- workers, Redis, and WebSocket gateway running normally.

Never use real user credentials or production data.

```sh
export LOAD_SESSION_ID="<published-session-uuid>"
export LOAD_EMAIL_TEMPLATE='load{index}@example.com'
export LOAD_PASSWORD='<load-test-only-password>'
export TARGET_HOST='https://test.example.com'
```

## Five-minute mixed API test

Fifty users execute one request approximately every second. Task weights are
7 reads to 3 registration writes. Each account remains below the default
registration-write rate limit under normal scheduling.

```sh
uv run locust -f tests/load/locustfile.py \
  --headless --tags mixed \
  --host "$TARGET_HOST" \
  --users 50 --spawn-rate 10 --run-time 5m \
  --csv load-results/mixed --html load-results/mixed.html
```

Acceptance:

- warm-up is excluded from the reported five-minute window;
- ordinary query and business-write P95 are each at most 1 second;
- unexpected 5xx responses are below 1%;
- expected 404/409/429 responses are reported separately and do not hide
  transport or server failures.

## Capacity/concurrency run

The command counter stops the run after 1,000 registration commands. Twenty
percent are exact idempotent replays. Run ten rounds after resetting
participations and session capacity to the same starting state.

```sh
export LOAD_STOP_AFTER_COMMANDS=1000
for round in $(seq 1 10); do
  uv run locust -f tests/load/locustfile.py \
    --headless --tags booking \
    --host "$TARGET_HOST" \
    --users 100 --spawn-rate 100 \
    --csv "load-results/concurrency-${round}"
  # Reset only through the approved disposable-dataset script before the next round.
done
```

After every round, query PostgreSQL and Prometheus and record:

- `capacity_violations` equals zero;
- occupied count never exceeds 20;
- no account has more than one active participation in the session;
- expected conflict and rate-limit counts;
- unexpected errors and lock timeouts.

## Evidence to retain

For every report include:

- date, git commit, environment, machine/node specifications;
- replica counts and CPU/memory requests and limits;
- PostgreSQL connection budget and observed connections;
- dataset size and exact Locust command;
- request mix, response-code counts, throughput, P50/P95/P99 and maximum;
- promotion-latency and commit-to-render observations;
- Grafana screenshots or exported panel data;
- correctness queries before and after the run.

Do not lower an acceptance threshold to make a failed run pass. Record the
cause, remediation, and rerun.
