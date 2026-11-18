from datetime import UTC, datetime, timedelta

import pytest

try:
    from app.domain.timing import (
        RegistrationStatus as R, allocation_deadline, is_overdue, registration_status,
    )
except ImportError as exc:
    pytest.skip(f"第 06 章未完成: {exc}", allow_module_level=True)

T = datetime(2026, 11, 15, 12, tzinfo=UTC)
H = timedelta(hours=1)


def test_window_boundaries():
    assert registration_status(T, T + H, T - timedelta(microseconds=1)) == R.NOT_OPEN
    assert registration_status(T, T + H, T) == R.OPEN
    assert registration_status(T, T + H, T + H) == R.CLOSED


def test_deadline_capped_by_close():
    assert allocation_deadline(T, 300, T + H) == T + timedelta(minutes=5)
    assert allocation_deadline(T, 300, T + timedelta(minutes=2)) == T + timedelta(minutes=2)


def test_overdue_is_inclusive():
    assert is_overdue(T, T) and not is_overdue(T, T - timedelta(microseconds=1))
