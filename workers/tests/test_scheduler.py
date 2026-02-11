"""Tests for recurring scheduler helpers."""

from kura_workers.scheduler import nightly_interval_hours


def test_nightly_interval_hours_default(monkeypatch):
    monkeypatch.delenv("KURA_NIGHTLY_REFIT_HOURS", raising=False)
    assert nightly_interval_hours() == 24


def test_nightly_interval_hours_clamps_to_positive(monkeypatch):
    monkeypatch.setenv("KURA_NIGHTLY_REFIT_HOURS", "-5")
    assert nightly_interval_hours() == 1


def test_nightly_interval_hours_invalid(monkeypatch):
    monkeypatch.setenv("KURA_NIGHTLY_REFIT_HOURS", "abc")
    assert nightly_interval_hours() == 24
