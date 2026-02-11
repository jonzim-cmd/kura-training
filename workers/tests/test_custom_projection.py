"""Tests for custom projection handler (Phase 3, Decision 10).

Unit tests using mock DB connections. Integration tests require a running database.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kura_workers.handlers.custom_projection import (
    _compute_categorized_tracking,
    _compute_field_tracking,
    _compute_rule,
    _load_active_rules,
    has_matching_custom_rules,
    recompute_matching_rules,
    update_custom_projections,
)
from kura_workers.rule_models import CategorizedTrackingRule, FieldTrackingRule


# ---------------------------------------------------------------------------
# Helper: mock DB rows
# ---------------------------------------------------------------------------


def _make_event(event_type: str, data: dict, ts: str = "2026-02-01T10:00:00+00:00", event_id: str = "evt-1"):
    """Create a mock event row."""
    return {
        "id": event_id,
        "event_type": event_type,
        "data": data,
        "timestamp": datetime.fromisoformat(ts),
    }


def _make_mock_cursor(rows):
    """Create an async mock cursor that returns given rows."""
    cursor = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=rows)
    cursor.fetchone = AsyncMock(return_value=rows[0] if rows else None)
    cursor.execute = AsyncMock()
    return cursor


class _MockCursorContext:
    """Context manager for mock cursor."""
    def __init__(self, cursor):
        self.cursor = cursor

    async def __aenter__(self):
        return self.cursor

    async def __aexit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# Test: _load_active_rules
# ---------------------------------------------------------------------------


class TestLoadActiveRules:
    @pytest.fixture
    def conn(self):
        conn = AsyncMock()
        return conn

    async def test_empty_rules(self, conn):
        cursor = _make_mock_cursor([])
        conn.cursor = MagicMock(return_value=_MockCursorContext(cursor))
        result = await _load_active_rules(conn, "user-1")
        assert result == {}

    async def test_single_created_rule(self, conn):
        rows = [
            {"event_type": "projection_rule.created", "data": {
                "name": "hrv_tracking", "type": "field_tracking",
                "source_events": ["sleep.logged"], "fields": ["hrv_rmssd"],
            }},
        ]
        cursor = _make_mock_cursor(rows)
        conn.cursor = MagicMock(return_value=_MockCursorContext(cursor))
        result = await _load_active_rules(conn, "user-1")
        assert "hrv_tracking" in result
        assert result["hrv_tracking"]["type"] == "field_tracking"

    async def test_archived_rule_removed(self, conn):
        rows = [
            {"event_type": "projection_rule.created", "data": {
                "name": "hrv_tracking", "type": "field_tracking",
                "source_events": ["sleep.logged"], "fields": ["hrv_rmssd"],
            }},
            {"event_type": "projection_rule.archived", "data": {"name": "hrv_tracking"}},
        ]
        cursor = _make_mock_cursor(rows)
        conn.cursor = MagicMock(return_value=_MockCursorContext(cursor))
        result = await _load_active_rules(conn, "user-1")
        assert result == {}

    async def test_recreated_after_archive(self, conn):
        rows = [
            {"event_type": "projection_rule.created", "data": {
                "name": "hrv", "type": "field_tracking",
                "source_events": ["sleep.logged"], "fields": ["hrv_rmssd"],
            }},
            {"event_type": "projection_rule.archived", "data": {"name": "hrv"}},
            {"event_type": "projection_rule.created", "data": {
                "name": "hrv", "type": "field_tracking",
                "source_events": ["sleep.logged"], "fields": ["hrv_rmssd", "deep_sleep_pct"],
            }},
        ]
        cursor = _make_mock_cursor(rows)
        conn.cursor = MagicMock(return_value=_MockCursorContext(cursor))
        result = await _load_active_rules(conn, "user-1")
        assert "hrv" in result
        assert "deep_sleep_pct" in result["hrv"]["fields"]


# ---------------------------------------------------------------------------
# Test: _compute_field_tracking
# ---------------------------------------------------------------------------


class TestComputeFieldTracking:
    @pytest.fixture
    def rule(self):
        return FieldTrackingRule(
            name="hrv_tracking",
            type="field_tracking",
            source_events=["sleep.logged"],
            fields=["hrv_rmssd", "deep_sleep_pct"],
        )

    async def test_empty_events(self, rule):
        conn = AsyncMock()
        cursor = _make_mock_cursor([])
        conn.cursor = MagicMock(return_value=_MockCursorContext(cursor))

        result = await _compute_field_tracking(conn, "user-1", rule, set())
        assert result["recent_entries"] == []
        assert result["weekly_summary"] == []
        assert result["all_time"] == {}
        assert result["data_quality"]["total_events_processed"] == 0

    async def test_basic_computation(self, rule):
        events = [
            _make_event("sleep.logged", {"hrv_rmssd": 55.0, "deep_sleep_pct": 18.0},
                        "2026-02-01T08:00:00+00:00", "evt-1"),
            _make_event("sleep.logged", {"hrv_rmssd": 60.0, "deep_sleep_pct": 20.0},
                        "2026-02-02T08:00:00+00:00", "evt-2"),
            _make_event("sleep.logged", {"hrv_rmssd": 50.0},
                        "2026-02-03T08:00:00+00:00", "evt-3"),
        ]
        conn = AsyncMock()
        cursor = _make_mock_cursor(events)
        conn.cursor = MagicMock(return_value=_MockCursorContext(cursor))

        result = await _compute_field_tracking(conn, "user-1", rule, set())

        assert result["data_quality"]["total_events_processed"] == 3
        assert result["data_quality"]["fields_present"]["hrv_rmssd"] == 3
        assert result["data_quality"]["fields_present"]["deep_sleep_pct"] == 2
        assert len(result["recent_entries"]) == 3
        assert result["recent_entries"][0]["date"] == "2026-02-01"
        assert result["recent_entries"][0]["hrv_rmssd"] == 55.0
        assert result["all_time"]["hrv_rmssd"]["count"] == 3
        assert result["all_time"]["hrv_rmssd"]["min"] == 50.0
        assert result["all_time"]["hrv_rmssd"]["max"] == 60.0
        assert len(result["weekly_summary"]) >= 1
        assert result["rule"]["name"] == "hrv_tracking"

    async def test_retracted_events_excluded(self, rule):
        events = [
            _make_event("sleep.logged", {"hrv_rmssd": 55.0}, "2026-02-01T08:00:00+00:00", "evt-1"),
            _make_event("sleep.logged", {"hrv_rmssd": 999.0}, "2026-02-02T08:00:00+00:00", "evt-2"),
        ]
        conn = AsyncMock()
        cursor = _make_mock_cursor(events)
        conn.cursor = MagicMock(return_value=_MockCursorContext(cursor))

        result = await _compute_field_tracking(conn, "user-1", rule, {"evt-2"})
        assert result["data_quality"]["total_events_processed"] == 1
        assert result["all_time"]["hrv_rmssd"]["count"] == 1

    async def test_recent_entries_limited_to_30(self, rule):
        # Spread across Jan + Feb to avoid invalid dates
        from datetime import date as dt_date, timedelta
        base = dt_date(2026, 1, 1)
        events = [
            _make_event("sleep.logged", {"hrv_rmssd": float(i)},
                        (base + timedelta(days=i)).isoformat() + "T08:00:00+00:00", f"evt-{i}")
            for i in range(40)
        ]
        conn = AsyncMock()
        cursor = _make_mock_cursor(events)
        conn.cursor = MagicMock(return_value=_MockCursorContext(cursor))

        result = await _compute_field_tracking(conn, "user-1", rule, set())
        assert len(result["recent_entries"]) == 30


# ---------------------------------------------------------------------------
# Test: _compute_categorized_tracking
# ---------------------------------------------------------------------------


class TestComputeCategorizedTracking:
    @pytest.fixture
    def rule(self):
        return CategorizedTrackingRule(
            name="supplement_tracking",
            type="categorized_tracking",
            source_events=["supplement.logged"],
            fields=["name", "dose_mg", "timing"],
            group_by="name",
        )

    async def test_empty_events(self, rule):
        conn = AsyncMock()
        cursor = _make_mock_cursor([])
        conn.cursor = MagicMock(return_value=_MockCursorContext(cursor))

        result = await _compute_categorized_tracking(conn, "user-1", rule, set())
        assert result["categories"] == {}
        assert result["data_quality"]["total_events_processed"] == 0

    async def test_basic_categorization(self, rule):
        events = [
            _make_event("supplement.logged", {"name": "Creatine", "dose_mg": 5000, "timing": "morning"},
                        "2026-02-01T08:00:00+00:00", "evt-1"),
            _make_event("supplement.logged", {"name": "Creatine", "dose_mg": 5000, "timing": "morning"},
                        "2026-02-02T08:00:00+00:00", "evt-2"),
            _make_event("supplement.logged", {"name": "Vitamin D", "dose_mg": 2000, "timing": "morning"},
                        "2026-02-01T08:00:00+00:00", "evt-3"),
        ]
        conn = AsyncMock()
        cursor = _make_mock_cursor(events)
        conn.cursor = MagicMock(return_value=_MockCursorContext(cursor))

        result = await _compute_categorized_tracking(conn, "user-1", rule, set())

        assert result["data_quality"]["total_events_processed"] == 3
        assert result["data_quality"]["categories_found"] == 2
        assert "creatine" in result["categories"]
        assert "vitamin d" in result["categories"]
        assert result["categories"]["creatine"]["count"] == 2
        assert result["categories"]["vitamin d"]["count"] == 1
        # dose_mg aggregation
        assert result["categories"]["creatine"]["fields"]["dose_mg"]["avg"] == 5000.0

    async def test_missing_group_by_field(self, rule):
        events = [
            _make_event("supplement.logged", {"dose_mg": 5000},
                        "2026-02-01T08:00:00+00:00", "evt-1"),
        ]
        conn = AsyncMock()
        cursor = _make_mock_cursor(events)
        conn.cursor = MagicMock(return_value=_MockCursorContext(cursor))

        result = await _compute_categorized_tracking(conn, "user-1", rule, set())
        assert "_unknown" in result["categories"]


# ---------------------------------------------------------------------------
# Test: _compute_rule (dispatch)
# ---------------------------------------------------------------------------


class TestComputeRule:
    async def test_invalid_rule_returns_none(self):
        conn = AsyncMock()
        result = await _compute_rule(conn, "user-1", {"name": "bad", "type": "unknown"}, set())
        assert result is None

    async def test_field_tracking_dispatched(self):
        conn = AsyncMock()
        cursor = _make_mock_cursor([])
        conn.cursor = MagicMock(return_value=_MockCursorContext(cursor))

        result = await _compute_rule(
            conn, "user-1",
            {"name": "test", "type": "field_tracking", "source_events": ["x"], "fields": ["y"]},
            set(),
        )
        assert result is not None
        assert result["rule"]["type"] == "field_tracking"

    async def test_categorized_tracking_dispatched(self):
        conn = AsyncMock()
        cursor = _make_mock_cursor([])
        conn.cursor = MagicMock(return_value=_MockCursorContext(cursor))

        result = await _compute_rule(
            conn, "user-1",
            {"name": "test", "type": "categorized_tracking",
             "source_events": ["x"], "fields": ["name", "dose"], "group_by": "name"},
            set(),
        )
        assert result is not None
        assert result["rule"]["type"] == "categorized_tracking"


# ---------------------------------------------------------------------------
# Test: has_matching_custom_rules
# ---------------------------------------------------------------------------


class TestHasMatchingCustomRules:
    async def test_no_projections(self):
        conn = AsyncMock()
        with patch("kura_workers.handlers.custom_projection.get_retracted_event_ids",
                   AsyncMock(return_value=set())), \
             patch("kura_workers.handlers.custom_projection._load_active_rules",
                   AsyncMock(return_value={})):
            result = await has_matching_custom_rules(conn, "user-1", "sleep.logged")
        assert result is False

    async def test_matching_rule(self):
        conn = AsyncMock()
        active = {
            "hrv_tracking": {
                "name": "hrv_tracking",
                "type": "field_tracking",
                "source_events": ["sleep.logged", "energy.logged"],
                "fields": ["hrv_rmssd"],
            }
        }
        with patch("kura_workers.handlers.custom_projection.get_retracted_event_ids",
                   AsyncMock(return_value=set())), \
             patch("kura_workers.handlers.custom_projection._load_active_rules",
                   AsyncMock(return_value=active)):
            result = await has_matching_custom_rules(conn, "user-1", "sleep.logged")
        assert result is True

    async def test_non_matching_rule(self):
        conn = AsyncMock()
        active = {
            "supplement_tracking": {
                "name": "supplement_tracking",
                "type": "categorized_tracking",
                "source_events": ["supplement.logged"],
                "fields": ["name", "dose_mg"],
                "group_by": "name",
            }
        }
        with patch("kura_workers.handlers.custom_projection.get_retracted_event_ids",
                   AsyncMock(return_value=set())), \
             patch("kura_workers.handlers.custom_projection._load_active_rules",
                   AsyncMock(return_value=active)):
            result = await has_matching_custom_rules(conn, "user-1", "sleep.logged")
        assert result is False
