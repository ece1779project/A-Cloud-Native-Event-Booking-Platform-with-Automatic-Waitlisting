# Event booking with automatic waitlisting

Attendees browse published events, hold one seat per session, and confirm it with simulated payment. When a session is full they join a first-come-first-served waitlist. If a hold expires, an attendee cancels, or an offer is declined, the earliest eligible waiter is offered the seat. Organizers manage events and sessions. Administrators manage accounts.

PostgreSQL decides seats, queue order, and deadlines. Redis applies rate limits and carries best-effort refresh signals. The browser treats WebSocket messages as signals to refetch; the REST API is the authority for what it displays.

## Layout

| Path | Role |
| --- | --- |
| `app/` | API, WebSocket gateway, and background workers, one image |
| `frontend/` | React application served by nginx |
| `doc/PRD.md` | Business rules and acceptance targets |
| `doc/TECHNICAL_DESIGN.md` | Implementation design |
| `doc/DATABASE_DESIGN.md` | Schema, transitions, and write rules |
| `doc/openapi.yaml` | HTTP contract |
| `deploy/` | Kubernetes manifests and the deployment runbook |
| `tests/load/` | Load-test procedure |

## Run locally

Copy `.env.example` to `.env`. Set `POSTGRES_PASSWORD` and an Ed25519 key pair for `JWT_PRIVATE_KEY` and `JWT_PUBLIC_KEY`. Optional `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` create the first administrator.

```sh
docker compose up --build
docker compose exec api /app/.venv/bin/python -m app.cli.bootstrap_admin
```

The site is at `http://localhost:5173`. The API is at `http://localhost:8000`. Grafana is at `http://localhost:3000`, using the admin credentials in `.env`.

Cloud deployment steps are in `deploy/README.md`.
