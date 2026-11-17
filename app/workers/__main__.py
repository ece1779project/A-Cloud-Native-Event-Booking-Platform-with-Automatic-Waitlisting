"""Worker process entry point: ``python -m app.workers``.

Starts every scan loop, serves health and metrics on ``WORKER_METRICS_PORT``,
and handles graceful shutdown.

Design: TECHNICAL_DESIGN.md Sections 11 and 13.3.
"""
