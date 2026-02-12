from datetime import date, datetime, timezone

from kura_workers.utils import next_fallback_session_key, normalize_temporal_point


class TestNormalizeTemporalPoint:
    def test_uses_event_timestamp_when_no_external_candidates(self):
        point = normalize_temporal_point(
            datetime(2026, 2, 8, 7, 30, tzinfo=timezone.utc),
            timezone_name="America/Los_Angeles",
        )

        assert point.source == "event.timestamp"
        assert point.local_date == date(2026, 2, 7)
        assert point.iso_week == "2026-W06"
        assert point.conflicts == ()

    def test_provider_timestamp_overrides_and_marks_event_store_drift(self):
        point = normalize_temporal_point(
            datetime(2026, 2, 8, 12, 20, tzinfo=timezone.utc),
            timezone_name="UTC",
            data={"provider_timestamp": "2026-02-08T12:00:00+00:00"},
        )

        assert point.source == "data.provider_timestamp"
        assert point.timestamp_utc == datetime(2026, 2, 8, 12, 0, tzinfo=timezone.utc)
        assert "event_store_drift" in point.conflicts

    def test_naive_timestamp_with_timezone_hint_is_normalized(self):
        point = normalize_temporal_point(
            datetime(2026, 2, 9, 7, 30, tzinfo=timezone.utc),
            timezone_name="America/Los_Angeles",
            data={
                "start_time": "2026-02-08T23:30:00",
                "timezone": "America/Los_Angeles",
            },
        )

        assert point.source == "data.start_time"
        assert point.local_date == date(2026, 2, 8)
        assert "naive_timestamp_assumed_timezone" in point.conflicts

    def test_provider_device_drift_is_reported(self):
        point = normalize_temporal_point(
            datetime(2026, 2, 8, 12, 0, tzinfo=timezone.utc),
            timezone_name="UTC",
            data={"provider_timestamp": "2026-02-08T12:00:00+00:00"},
            metadata={"device_timestamp": "2026-02-08T12:12:00+00:00"},
        )

        assert "provider_device_drift" in point.conflicts


class TestFallbackSessionBoundary:
    def test_same_day_keeps_session_key(self):
        key, state = next_fallback_session_key(
            local_date=date(2026, 2, 8),
            timestamp_utc=datetime(2026, 2, 8, 18, 0, tzinfo=timezone.utc),
            state=None,
        )
        next_key, _ = next_fallback_session_key(
            local_date=date(2026, 2, 8),
            timestamp_utc=datetime(2026, 2, 8, 19, 30, tzinfo=timezone.utc),
            state=state,
        )

        assert key == "2026-02-08"
        assert next_key == "2026-02-08"

    def test_cross_midnight_short_gap_keeps_same_session(self):
        key, state = next_fallback_session_key(
            local_date=date(2026, 2, 8),
            timestamp_utc=datetime(2026, 2, 8, 23, 30, tzinfo=timezone.utc),
            state=None,
        )
        next_key, _ = next_fallback_session_key(
            local_date=date(2026, 2, 9),
            timestamp_utc=datetime(2026, 2, 9, 0, 40, tzinfo=timezone.utc),
            state=state,
        )

        assert key == "2026-02-08"
        assert next_key == "2026-02-08"

    def test_cross_midnight_large_gap_starts_new_session(self):
        _, state = next_fallback_session_key(
            local_date=date(2026, 2, 8),
            timestamp_utc=datetime(2026, 2, 8, 23, 30, tzinfo=timezone.utc),
            state=None,
        )
        next_key, _ = next_fallback_session_key(
            local_date=date(2026, 2, 9),
            timestamp_utc=datetime(2026, 2, 9, 5, 45, tzinfo=timezone.utc),
            state=state,
        )

        assert next_key == "2026-02-09"

    def test_cross_midnight_exceeds_max_duration_starts_new_session(self):
        _, state = next_fallback_session_key(
            local_date=date(2026, 2, 8),
            timestamp_utc=datetime(2026, 2, 8, 16, 0, tzinfo=timezone.utc),
            state=None,
            overnight_gap_hours=12.0,
            max_session_hours=6.0,
        )
        next_key, _ = next_fallback_session_key(
            local_date=date(2026, 2, 9),
            timestamp_utc=datetime(2026, 2, 9, 0, 30, tzinfo=timezone.utc),
            state=state,
            overnight_gap_hours=12.0,
            max_session_hours=6.0,
        )

        assert next_key == "2026-02-09"
