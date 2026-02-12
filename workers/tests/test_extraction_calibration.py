"""Unit tests for extraction calibration + drift pipeline (2zc.5)."""

from datetime import UTC, datetime

from kura_workers.extraction_calibration import (
    CalibrationRecord,
    ExtractionCalibrationSettings,
    build_calibration_metrics,
    build_underperforming_classes,
)


def _settings() -> ExtractionCalibrationSettings:
    return ExtractionCalibrationSettings(
        window_days=30,
        high_conf_threshold=0.86,
        min_samples_for_status=2,
        brier_monitor_max=0.20,
        brier_degraded_max=0.30,
        precision_monitor_min=0.70,
        precision_degraded_min=0.55,
        drift_alert_delta_brier=0.06,
    )


def _record(
    *,
    ts: datetime,
    confidence: float,
    label: float,
    claim_class: str = "set_context.rest_seconds",
    parser_version: str = "mention_parser.v1",
) -> CalibrationRecord:
    return CalibrationRecord(
        captured_at=ts,
        claim_class=claim_class,
        parser_version=parser_version,
        confidence=confidence,
        label=label,
    )


def test_build_calibration_metrics_computes_brier_precision_and_status():
    records = [
        _record(ts=datetime(2026, 2, 12, 10, 0, tzinfo=UTC), confidence=0.95, label=0.0),
        _record(ts=datetime(2026, 2, 12, 10, 5, tzinfo=UTC), confidence=0.90, label=0.0),
        _record(ts=datetime(2026, 2, 12, 10, 10, tzinfo=UTC), confidence=0.85, label=1.0),
    ]
    metrics = build_calibration_metrics(records, settings=_settings())
    week_row = next(row for row in metrics if row["period_granularity"] == "week")

    assert week_row["sample_count"] == 3
    assert week_row["correct_count"] == 1
    assert week_row["incorrect_count"] == 2
    assert week_row["brier_score"] > 0.30
    assert week_row["status"] == "degraded"


def test_build_calibration_metrics_marks_drift_alert_on_brier_jump():
    records = [
        _record(ts=datetime(2026, 2, 1, 10, 0, tzinfo=UTC), confidence=0.9, label=1.0),
        _record(ts=datetime(2026, 2, 1, 10, 5, tzinfo=UTC), confidence=0.88, label=1.0),
        _record(ts=datetime(2026, 2, 8, 10, 0, tzinfo=UTC), confidence=0.95, label=0.0),
        _record(ts=datetime(2026, 2, 8, 10, 5, tzinfo=UTC), confidence=0.90, label=0.0),
    ]
    metrics = build_calibration_metrics(records, settings=_settings())
    weekly_rows = [
        row for row in metrics
        if row["period_granularity"] == "week"
        and row["claim_class"] == "set_context.rest_seconds"
    ]
    latest = sorted(weekly_rows, key=lambda row: row["period_key"])[-1]
    assert latest["drift_status"] == "drift_alert"
    assert latest["drift_delta_brier"] is not None
    assert latest["drift_delta_brier"] >= 0.06


def test_build_underperforming_classes_uses_latest_week_only():
    records = [
        _record(ts=datetime(2026, 2, 1, 10, 0, tzinfo=UTC), confidence=0.9, label=1.0),
        _record(ts=datetime(2026, 2, 1, 10, 5, tzinfo=UTC), confidence=0.9, label=1.0),
        _record(ts=datetime(2026, 2, 8, 10, 0, tzinfo=UTC), confidence=0.95, label=0.0),
        _record(ts=datetime(2026, 2, 8, 10, 5, tzinfo=UTC), confidence=0.90, label=0.0),
    ]
    metrics = build_calibration_metrics(records, settings=_settings())
    underperforming = build_underperforming_classes(metrics)

    assert len(underperforming) >= 1
    period_keys = {row["period_key"] for row in underperforming}
    assert len(period_keys) == 1
    first = underperforming[0]
    assert first["claim_class"] == "set_context.rest_seconds"
    assert first["status"] in {"underperforming", "drift_alert"}
