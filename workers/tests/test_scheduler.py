"""Tests for recurring scheduler helpers."""

from datetime import datetime, timedelta, timezone

import pytest

from kura_workers.scheduler import due_run_count, nightly_interval_hours


def test_nightly_interval_hours_default(monkeypatch):
    monkeypatch.delenv("KURA_NIGHTLY_REFIT_HOURS", raising=False)
    assert nightly_interval_hours() == 24


def test_nightly_interval_hours_clamps_to_positive(monkeypatch):
    monkeypatch.setenv("KURA_NIGHTLY_REFIT_HOURS", "-5")
    assert nightly_interval_hours() == 1


def test_nightly_interval_hours_invalid(monkeypatch):
    monkeypatch.setenv("KURA_NIGHTLY_REFIT_HOURS", "abc")
    assert nightly_interval_hours() == 24


def test_due_run_count_zero_when_next_run_in_future():
    now = datetime(2026, 2, 11, 10, 0, tzinfo=timezone.utc)
    next_run_at = now + timedelta(hours=2)
    assert due_run_count(now, next_run_at, 1) == 0


def test_due_run_count_includes_missed_slots():
    now = datetime(2026, 2, 11, 10, 0, tzinfo=timezone.utc)
    next_run_at = now - timedelta(hours=3)
    assert due_run_count(now, next_run_at, 1) == 4


def test_due_run_count_rejects_non_positive_interval():
    now = datetime(2026, 2, 11, 10, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        due_run_count(now, now, 0)
