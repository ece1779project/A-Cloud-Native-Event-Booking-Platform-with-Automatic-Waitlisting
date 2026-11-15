"""SeatFlow backend package.

One package and one container image serve three process types, each started
by a different command:

- API:               ``uvicorn app.main:app``
- WebSocket gateway: ``uvicorn app.realtime.gateway.main:app``
- Workers:           ``python -m app.workers``

One-off commands live in ``app.cli``.

Import direction (a module may import only from layers below it):

    api, workers, realtime.gateway, cli     entry layers
    services                                business transactions
    realtime.publisher, realtime.tickets    post-commit Redis side effects
    schemas, models, db                     contracts and persistence
    domain, core                            leaves; import nothing from app

Design: TECHNICAL_DESIGN.md Section 5.
"""
