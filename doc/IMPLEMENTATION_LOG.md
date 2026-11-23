# Implementation summary

Behavior and contracts stay in `TECHNICAL_DESIGN.md`.

The realtime path, attendee and management frontend, local Compose stack, and Kubernetes manifests are on `feat/realtime-frontend-deploy`. The service name is `ticketbooking`: WebSocket subprotocol `ticketbooking.v1`, Redis prefix `ticketbooking:v1:`, and JWT issuer `ticketbooking`.

## What is in place

- Browsers get a 30-second single-use ticket, then connect to `/ws/v1`. Messages are refresh signals, published after the database commit, in public, private, then management order. Each gateway replica keeps one Redis pattern subscription.
- The React app covers the event list, event details, My Registrations, management, and admin accounts. Nginx serves the app and proxies `/api/` and `/ws/` on the same origin. While disconnected the page polls every 10 seconds; while connected it reconciles every 60 seconds.
- An open event page updates its seat count without a reload. A newly published event appears on the list without a reload. Disabling an account signs that browser out. A confirmed registration does not show a response deadline. A cancelled session is labeled on the public, registration, and management pages.
- My Registrations shows one card per session: the active attempt, or the newest finished attempt when there is no active one. The API still returns every attempt. Holds and offers stay at the top.
- Compose runs the API, gateway, worker, frontend, PostgreSQL, Redis, Prometheus, and Grafana. Kubernetes base, minikube, and DOKS manifests are rendered only; they have not been applied.
- Logs are JSON on stdout, with bearer tokens and tickets redacted. Metrics are at `:8000/metrics`, `:8001/metrics`, and `:9100/metrics`. Grafana is at `:3000` (`admin` / `admin`), dashboard **Ticketbooking Overview**. Open WebSockets is the number of browser sockets, refreshed about every 15 seconds. One gateway process is a separate target, not that number.

## What was checked

- Two browsers: login, book, simulated-payment confirm, waitlist promotion, decline, and publishing a new event. The other window updated without a manual reload.
- Disable then re-enable requires signing in again. Cancelled-session text shows on the three pages above.
- An account with several cancelled attempts on one session now sees a single My Registrations card.
- Rename-related unit and contract tests passed (31). The registration-list tests passed (3).

## Not done yet

- Integration tests, Locust runs, and a real minikube or DOKS deployment.
- Editing an event title, description, or location does not push a realtime message.
- The live-update fixes and the `ticketbooking` rename are in `0ed0ba5`.

