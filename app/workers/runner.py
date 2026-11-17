"""The scan-loop scaffold shared by all scans: interval, bounded batch,
backoff on dependency failure, cancellation and per-scan metrics. A finished
pass records ``worker_last_scan_timestamp_seconds``.

Design: TECHNICAL_DESIGN.md Sections 11 and 14.
"""
