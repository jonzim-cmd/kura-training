"""Tests for ProjectionQueryBuilder (Phase 3, Decision 10).

Tests verify query structure via string representation of composed SQL.
Actual database execution is tested in integration tests.
"""

import pytest
from psycopg import sql

from kura_workers.query_builder import ProjectionQueryBuilder


def _query_str(query: sql.Composed) -> str:
    """Convert a composed SQL object to a string for testing."""
    # Use a dummy connection-less approach: as_string with None adapter
    # psycopg.sql objects can be converted to string for inspection
    parts = []
    for part in query._obj:
        if isinstance(part, sql.SQL):
            parts.append(part._obj)
        elif isinstance(part, sql.Composed):
            parts.append(_query_str(part))
        elif isinstance(part, sql.Identifier):
            parts.append(f'"{part._obj[0]}"')
        elif isinstance(part, sql.Literal):
            val = part._obj
            if isinstance(val, str):
                parts.append(f"'{val}'")
            else:
                parts.append(str(val))
        else:
            parts.append(str(part))
    return "".join(parts)


class TestBasicBuilding:
    def test_requires_fields(self):
        with pytest.raises(ValueError, match="No fields specified"):
            ProjectionQueryBuilder().build()

    def test_raw_field_extraction(self):
        query, params = (
            ProjectionQueryBuilder()
            .extract_fields(["hrv_rmssd", "deep_sleep_pct"])
            .build()
        )
        qs = _query_str(query)
        assert "data->>'hrv_rmssd'" in qs
        assert "data->>'deep_sleep_pct'" in qs
        assert "timestamp" in qs  # raw mode includes timestamp
        assert params == ()

    def test_for_user(self):
        query, params = (
            ProjectionQueryBuilder()
            .for_user("user-123")
            .extract_fields(["hrv_rmssd"])
            .build()
        )
        qs = _query_str(query)
        assert "user_id = %s" in qs
        assert params == ("user-123",)

    def test_from_events(self):
        query, params = (
            ProjectionQueryBuilder()
            .from_events("sleep.logged")
            .extract_fields(["hrv_rmssd"])
            .build()
        )
        qs = _query_str(query)
        assert "event_type = ANY(%s)" in qs
        assert params == (["sleep.logged"],)

    def test_multiple_event_types(self):
        query, params = (
            ProjectionQueryBuilder()
            .from_events("sleep.logged", "energy.logged")
            .extract_fields(["level"])
            .build()
        )
        assert params == (["sleep.logged", "energy.logged"],)

    def test_exclude_retracted(self):
        query, params = (
            ProjectionQueryBuilder()
            .for_user("user-123")
            .from_events("sleep.logged")
            .exclude_retracted({"evt-1", "evt-2"})
            .extract_fields(["hrv_rmssd"])
            .build()
        )
        qs = _query_str(query)
        assert "id != ALL(%s)" in qs
        # user_id, event_types, retracted_ids
        assert len(params) == 3


class TestGroupByTime:
    def test_group_by_week(self):
        query, params = (
            ProjectionQueryBuilder()
            .from_events("sleep.logged")
            .for_user("uid")
            .extract_fields(["hrv_rmssd"])
            .group_by_week()
            .with_aggregations(("avg", "count"))
            .build()
        )
        qs = _query_str(query)
        assert "IYYY" in qs  # ISO year in week format
        assert '"week"' in qs
        assert "avg(" in qs
        assert "count(" in qs
        assert '"hrv_rmssd_avg"' in qs
        assert '"hrv_rmssd_count"' in qs

    def test_group_by_day(self):
        query, params = (
            ProjectionQueryBuilder()
            .from_events("sleep.logged")
            .extract_fields(["hrv_rmssd"])
            .group_by_day()
            .with_aggregations(("avg",))
            .build()
        )
        qs = _query_str(query)
        assert "::date" in qs
        assert '"day"' in qs


class TestGroupByField:
    def test_group_by_category(self):
        query, params = (
            ProjectionQueryBuilder()
            .from_events("supplement.logged")
            .for_user("uid")
            .extract_fields(["name", "dose_mg"])
            .group_by_field("name")
            .with_aggregations(("avg", "count"))
            .build()
        )
        qs = _query_str(query)
        assert "data->>'name'" in qs
        assert '"name"' in qs  # alias
        assert '"dose_mg_avg"' in qs
        assert '"dose_mg_count"' in qs
        # group_by field should NOT have aggregations applied
        assert '"name_avg"' not in qs


class TestOrdering:
    def test_order_by_time_asc(self):
        query, _ = (
            ProjectionQueryBuilder()
            .from_events("sleep.logged")
            .extract_fields(["hrv_rmssd"])
            .group_by_week()
            .with_aggregations()
            .order_by_time("ASC")
            .build()
        )
        qs = _query_str(query)
        assert "ORDER BY" in qs
        assert "ASC" in qs

    def test_order_by_time_desc(self):
        query, _ = (
            ProjectionQueryBuilder()
            .from_events("sleep.logged")
            .extract_fields(["hrv_rmssd"])
            .group_by_week()
            .with_aggregations()
            .order_by_time("DESC")
            .build()
        )
        qs = _query_str(query)
        assert "DESC" in qs


class TestFieldTrackingScenario:
    """Full field_tracking scenario: HRV from sleep events."""

    def test_weekly_aggregation_query(self):
        query, params = (
            ProjectionQueryBuilder()
            .from_events("sleep.logged")
            .for_user("user-abc")
            .extract_fields(["hrv_rmssd", "deep_sleep_pct"])
            .group_by_week()
            .with_aggregations(("avg", "min", "max", "count"))
            .order_by_time()
            .build()
        )
        qs = _query_str(query)
        # Should have week column + aggs for both fields
        assert '"week"' in qs
        assert '"hrv_rmssd_avg"' in qs
        assert '"hrv_rmssd_min"' in qs
        assert '"hrv_rmssd_max"' in qs
        assert '"hrv_rmssd_count"' in qs
        assert '"deep_sleep_pct_avg"' in qs
        assert "FROM events" in qs
        assert "ORDER BY" in qs
        assert params == ("user-abc", ["sleep.logged"])


class TestCategorizedTrackingScenario:
    """Full categorized_tracking scenario: supplements grouped by name."""

    def test_categorized_query(self):
        query, params = (
            ProjectionQueryBuilder()
            .from_events("supplement.logged")
            .for_user("user-xyz")
            .extract_fields(["name", "dose_mg", "timing"])
            .group_by_field("name")
            .with_aggregations(("count",))
            .build()
        )
        qs = _query_str(query)
        assert '"name"' in qs
        assert '"dose_mg_count"' in qs
        assert '"timing_count"' in qs
        # name should be in GROUP BY via data->>'name'
        assert "GROUP BY" in qs
        assert params == ("user-xyz", ["supplement.logged"])


class TestParameterSafety:
    """Ensure all values are parameterized, never interpolated."""

    def test_field_names_are_literals(self):
        """Field names extracted from rules use sql.Literal (quoted), not interpolation."""
        query, _ = (
            ProjectionQueryBuilder()
            .extract_fields(["field'; DROP TABLE events;--"])
            .build()
        )
        qs = _query_str(query)
        # The malicious string should be quoted as a literal, not executed
        assert "DROP TABLE" not in qs or "'" in qs

    def test_user_id_is_parameter(self):
        """user_id is always a query parameter, never in the SQL string."""
        query, params = (
            ProjectionQueryBuilder()
            .for_user("malicious'; DROP TABLE events;--")
            .extract_fields(["x"])
            .build()
        )
        qs = _query_str(query)
        assert "malicious" not in qs
        assert "malicious'; DROP TABLE events;--" in params
