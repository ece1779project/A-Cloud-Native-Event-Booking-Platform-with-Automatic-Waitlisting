"""The scan-loop scaffold shared by all scans: interval, bounded batch,
backoff on dependency failure, cancellation and per-scan metrics.

Design: TECHNICAL_DESIGN.md Section 11.
"""
