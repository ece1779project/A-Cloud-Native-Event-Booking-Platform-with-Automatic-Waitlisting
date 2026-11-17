"""Prometheus metric definitions shared by the API, the workers and the
gateway. Labels use bounded values only; IDs belong in logs. Includes
``worker_last_scan_timestamp_seconds``, one series per scan loop.

Design: TECHNICAL_DESIGN.md Section 14.
"""
