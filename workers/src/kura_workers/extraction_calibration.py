"""Extraction confidence calibration + drift monitoring (2zc.5)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
import os
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractionCalibrationSettings:
    window_days: int
    high_conf_threshold: float
    min_samples_for_status: int
    brier_monitor_max: float
    brier_degraded_max: float
    precision_monitor_min: float
    precision_degraded_min: float
    drift_alert_delta_brier: float


@dataclass(frozen=True)
class CalibrationRecord:
    captured_at: datetime
    claim_class: str
    parser_version: str
    confidence: float
    label: float


def _int_env(name: str, default: int, minimum: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return max(minimum, int(raw))
    except ValueError:
        return max(minimum, default)


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        parsed = float(raw)
    except ValueError:
        parsed = default
    return min(maximum, max(minimum, parsed))


def extraction_calibration_settings() -> ExtractionCalibrationSettings:
    return ExtractionCalibrationSettings(
        window_days=_int_env("KURA_EXTRACTION_CALIBRATION_WINDOW_DAYS", 30, 1),
        high_conf_threshold=_float_env(
            "KURA_EXTRACTION_HIGH_CONF_THRESHOLD", 0.86, 0.0, 1.0
        ),
        min_samples_for_status=_int_env(
            "KURA_EXTRACTION_MIN_SAMPLES_FOR_STATUS", 3, 1
        ),
        brier_monitor_max=_float_env(
            "KURA_EXTRACTION_BRIER_MONITOR_MAX", 0.20, 0.0, 2.0
        ),
        brier_degraded_max=_float_env(
            "KURA_EXTRACTION_BRIER_DEGRADED_MAX", 0.30, 0.0, 2.0
        ),
        precision_monitor_min=_float_env(
            "KURA_EXTRACTION_PRECISION_MONITOR_MIN", 0.70, 0.0, 1.0
        ),
        precision_degraded_min=_float_env(
            "KURA_EXTRACTION_PRECISION_DEGRADED_MIN", 0.55, 0.0, 1.0
        ),
        drift_alert_delta_brier=_float_env(
            "KURA_EXTRACTION_DRIFT_DELTA_BRIER_ALERT", 0.06, 0.0, 2.0
        ),
    )


def _normalize_text(value: Any, fallback: str = "unknown") -> str:
    if not isinstance(value, str):
        return fallback
    normalized = value.strip()
    return normalized if normalized else fallback


def _clamp_confidence(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, score))


def _period_key(captured_at: datetime, granularity: str) -> str:
    if granularity == "day":
        return captured_at.date().isoformat()
    iso_year, iso_week, _ = captured_at.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _confidence_band(score: float) -> str:
    if score >= 0.86:
        return "high"
    if score >= 0.60:
        return "medium"
    return "low"


def _field_from_claim_class(claim_class: str) -> str:
    if "." in claim_class:
        return claim_class.rsplit(".", 1)[-1].strip()
    return claim_class.strip()


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / float(len(values))


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0.0:
        return None
    return numerator / denominator


def _status_from_metrics(
    *,
    sample_count: int,
    brier_score: float,
    precision_high_conf: float | None,
    high_conf_count: int,
    settings: ExtractionCalibrationSettings,
) -> str:
    if sample_count < settings.min_samples_for_status:
        return "monitor"
    if high_conf_count == 0:
        return "monitor"

    if brier_score >= settings.brier_degraded_max:
        return "degraded"
    if (
        precision_high_conf is not None
        and precision_high_conf < settings.precision_degraded_min
    ):
        return "degraded"

    if brier_score >= settings.brier_monitor_max:
        return "monitor"
    if (
        precision_high_conf is not None
        and precision_high_conf < settings.precision_monitor_min
    ):
        return "monitor"
    return "healthy"


def build_calibration_metrics(
    records: list[CalibrationRecord],
    *,
    settings: ExtractionCalibrationSettings,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[CalibrationRecord]] = defaultdict(list)
    for record in records:
        for granularity in ("day", "week"):
            grouped[
                (
                    granularity,
                    _period_key(record.captured_at, granularity),
                    record.claim_class,
                    record.parser_version,
                )
            ].append(record)

    metrics: list[dict[str, Any]] = []
    for key in sorted(grouped.keys()):
        granularity, period_key, claim_class, parser_version = key
        bucket = sorted(grouped[key], key=lambda item: item.captured_at)
        sample_count = len(bucket)
        correct_count = sum(1 for item in bucket if item.label >= 0.5)
        incorrect_count = sample_count - correct_count
        avg_confidence = _mean([item.confidence for item in bucket])
        brier_score = _mean([(item.confidence - item.label) ** 2 for item in bucket])

        high_conf = [item for item in bucket if item.confidence >= settings.high_conf_threshold]
        high_conf_count = len(high_conf)
        high_conf_correct = sum(1 for item in high_conf if item.label >= 0.5)
        precision_high_conf = _safe_ratio(float(high_conf_correct), float(high_conf_count))
        recall_high_conf = _safe_ratio(float(high_conf_correct), float(correct_count))

        band_stats: dict[str, dict[str, Any]] = {}
        for band in ("high", "medium", "low"):
            band_items = [item for item in bucket if _confidence_band(item.confidence) == band]
            band_total = len(band_items)
            band_correct = sum(1 for item in band_items if item.label >= 0.5)
            band_stats[band] = {
                "count": band_total,
                "precision": (
                    round(float(band_correct) / float(band_total), 6)
                    if band_total > 0
                    else None
                ),
            }

        status = _status_from_metrics(
            sample_count=sample_count,
            brier_score=brier_score,
            precision_high_conf=precision_high_conf,
            high_conf_count=high_conf_count,
            settings=settings,
        )
        metrics.append(
            {
                "period_granularity": granularity,
                "period_key": period_key,
                "claim_class": claim_class,
                "parser_version": parser_version,
                "status": status,
                "drift_status": "insufficient_history",
                "drift_delta_brier": None,
                "sample_count": sample_count,
                "correct_count": correct_count,
                "incorrect_count": incorrect_count,
                "avg_confidence": round(avg_confidence, 6),
                "brier_score": round(brier_score, 6),
                "precision_high_conf": (
                    round(precision_high_conf, 6)
                    if precision_high_conf is not None
                    else None
                ),
                "recall_high_conf": (
                    round(recall_high_conf, 6) if recall_high_conf is not None else None
                ),
                "metric_data": {
                    "high_conf_threshold": settings.high_conf_threshold,
                    "confidence_calibration": band_stats,
                },
            }
        )

    by_stream: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for index, row in enumerate(metrics):
        by_stream[
            (
                row["period_granularity"],
                row["claim_class"],
                row["parser_version"],
            )
        ].append(index)

    for indices in by_stream.values():
        ordered = sorted(indices, key=lambda idx: metrics[idx]["period_key"])
        previous_brier: float | None = None
        for idx in ordered:
            current_brier = float(metrics[idx]["brier_score"])
            if previous_brier is None:
                metrics[idx]["drift_status"] = "insufficient_history"
                metrics[idx]["drift_delta_brier"] = None
                previous_brier = current_brier
                continue

            delta = current_brier - previous_brier
            metrics[idx]["drift_delta_brier"] = round(delta, 6)
            if delta >= settings.drift_alert_delta_brier:
                metrics[idx]["drift_status"] = "drift_alert"
            else:
                metrics[idx]["drift_status"] = "stable"
            previous_brier = current_brier

    return sorted(
        metrics,
        key=lambda row: (
            row["period_granularity"],
            row["period_key"],
            row["claim_class"],
            row["parser_version"],
        ),
    )


def build_underperforming_classes(
    metrics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    weekly = [row for row in metrics if row["period_granularity"] == "week"]
    if not weekly:
        return []

    latest_period = max(row["period_key"] for row in weekly)
    current = [row for row in weekly if row["period_key"] == latest_period]

    out: list[dict[str, Any]] = []
    for row in current:
        status = str(row["status"])
        drift_status = str(row["drift_status"])
        if status == "healthy" and drift_status != "drift_alert":
            continue
        report_status = "drift_alert" if drift_status == "drift_alert" else "underperforming"
        out.append(
            {
                "period_key": latest_period,
                "claim_class": row["claim_class"],
                "parser_version": row["parser_version"],
                "status": report_status,
                "brier_score": row["brier_score"],
                "precision_high_conf": row["precision_high_conf"],
                "sample_count": row["sample_count"],
                "details": {
                    "calibration_status": status,
                    "drift_status": drift_status,
                    "drift_delta_brier": row["drift_delta_brier"],
                    "recall_high_conf": row["recall_high_conf"],
                },
            }
        )

    return sorted(
        out,
        key=lambda row: (
            0 if row["status"] == "drift_alert" else 1,
            -float(row["brier_score"]),
            row["claim_class"],
            row["parser_version"],
        ),
    )


async def _table_exists(conn: psycopg.AsyncConnection[Any], table_name: str) -> bool:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (table_name,))
        row = await cur.fetchone()
    return bool(row and row.get("present"))


async def _load_retracted_target_event_ids(
    conn: psycopg.AsyncConnection[Any],
    *,
    window_days: int,
) -> set[str]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT data->>'retracted_event_id' AS retracted_event_id
            FROM events
            WHERE event_type = 'event.retracted'
              AND timestamp >= NOW() - make_interval(days => %s)
            """,
            (window_days,),
        )
        rows = await cur.fetchall()
    return {
        str(row["retracted_event_id"])
        for row in rows
        if row.get("retracted_event_id")
    }


async def _load_corrected_fields_by_target_event(
    conn: psycopg.AsyncConnection[Any],
    *,
    window_days: int,
) -> dict[str, set[str]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT data
            FROM events
            WHERE event_type = 'set.corrected'
              AND timestamp >= NOW() - make_interval(days => %s)
            ORDER BY timestamp ASC, id ASC
            """,
            (window_days,),
        )
        rows = await cur.fetchall()

    corrected: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        data = row.get("data")
        if not isinstance(data, dict):
            continue
        target_event_id = _normalize_text(data.get("target_event_id"), "")
        changed_fields = data.get("changed_fields")
        if not target_event_id or not isinstance(changed_fields, dict):
            continue
        corrected[target_event_id].update(
            field.strip()
            for field in changed_fields.keys()
            if isinstance(field, str) and field.strip()
        )
    return corrected


async def _load_claim_rows(
    conn: psycopg.AsyncConnection[Any],
    *,
    window_days: int,
) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT e.id::text AS event_id, e.timestamp, e.data
            FROM events e
            WHERE e.event_type = 'evidence.claim.logged'
              AND e.timestamp >= NOW() - make_interval(days => %s)
              AND NOT EXISTS (
                  SELECT 1
                  FROM events r
                  WHERE r.event_type = 'event.retracted'
                    AND r.data->>'retracted_event_id' = e.id::text
              )
            ORDER BY e.timestamp ASC, e.id ASC
            """,
            (window_days,),
        )
        return await cur.fetchall()


def _build_calibration_records(
    claim_rows: list[dict[str, Any]],
    *,
    retracted_target_event_ids: set[str],
    corrected_fields_by_target: dict[str, set[str]],
) -> list[CalibrationRecord]:
    records: list[CalibrationRecord] = []

    for row in claim_rows:
        data = row.get("data")
        if not isinstance(data, dict):
            continue
        claim_class = _normalize_text(data.get("claim_type"), "")
        if not claim_class:
            continue
        confidence = _clamp_confidence(data.get("confidence"))
        provenance = data.get("provenance")
        if not isinstance(provenance, dict):
            provenance = {}
        parser_version = _normalize_text(provenance.get("parser_version"))
        lineage = data.get("lineage")
        if not isinstance(lineage, dict):
            lineage = {}
        target_event_id = _normalize_text(lineage.get("event_id"), "")
        field_name = _field_from_claim_class(claim_class)

        label = 1.0
        if target_event_id and target_event_id in retracted_target_event_ids:
            label = 0.0
        elif (
            target_event_id
            and field_name
            and field_name in corrected_fields_by_target.get(target_event_id, set())
        ):
            label = 0.0

        timestamp = row.get("timestamp")
        if not isinstance(timestamp, datetime):
            continue
        captured_at = timestamp.astimezone(UTC) if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)
        records.append(
            CalibrationRecord(
                captured_at=captured_at,
                claim_class=claim_class,
                parser_version=parser_version,
                confidence=confidence,
                label=label,
            )
        )
    return records


async def _record_run(
    conn: psycopg.AsyncConnection[Any],
    *,
    status: str,
    settings: ExtractionCalibrationSettings,
    total_claims: int,
    considered_claims: int,
    metrics_written: int,
    underperforming_written: int,
    details: dict[str, Any],
    started_at: datetime,
) -> None:
    if not await _table_exists(conn, "extraction_calibration_runs"):
        return
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO extraction_calibration_runs (
                status,
                window_days,
                total_claims,
                considered_claims,
                metrics_written,
                underperforming_written,
                details,
                started_at,
                completed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                status,
                settings.window_days,
                total_claims,
                considered_claims,
                metrics_written,
                underperforming_written,
                Json(details),
                started_at,
            ),
        )


async def refresh_extraction_calibration(
    conn: psycopg.AsyncConnection[Any],
) -> dict[str, Any]:
    """Refresh extraction calibration metrics and underperforming class report."""
    started_at = datetime.now(UTC)
    settings = extraction_calibration_settings()

    if not await _table_exists(conn, "extraction_calibration_metrics"):
        summary = {
            "status": "skipped",
            "reason": "extraction_calibration_metrics_table_missing",
            "metrics_written": 0,
            "underperforming_written": 0,
        }
        await _record_run(
            conn,
            status="skipped",
            settings=settings,
            total_claims=0,
            considered_claims=0,
            metrics_written=0,
            underperforming_written=0,
            details=summary,
            started_at=started_at,
        )
        return summary

    claim_rows = await _load_claim_rows(conn, window_days=settings.window_days)
    retracted_target_event_ids = await _load_retracted_target_event_ids(
        conn,
        window_days=settings.window_days,
    )
    corrected_fields_by_target = await _load_corrected_fields_by_target_event(
        conn,
        window_days=settings.window_days,
    )
    records = _build_calibration_records(
        claim_rows,
        retracted_target_event_ids=retracted_target_event_ids,
        corrected_fields_by_target=corrected_fields_by_target,
    )
    metrics = build_calibration_metrics(records, settings=settings)
    underperforming = build_underperforming_classes(metrics)

    async with conn.cursor() as cur:
        await cur.execute("DELETE FROM extraction_calibration_metrics")
        await cur.execute("DELETE FROM extraction_underperforming_classes")

        for row in metrics:
            await cur.execute(
                """
                INSERT INTO extraction_calibration_metrics (
                    period_granularity,
                    period_key,
                    claim_class,
                    parser_version,
                    status,
                    drift_status,
                    drift_delta_brier,
                    sample_count,
                    correct_count,
                    incorrect_count,
                    avg_confidence,
                    brier_score,
                    precision_high_conf,
                    recall_high_conf,
                    metric_data,
                    computed_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                """,
                (
                    row["period_granularity"],
                    row["period_key"],
                    row["claim_class"],
                    row["parser_version"],
                    row["status"],
                    row["drift_status"],
                    row["drift_delta_brier"],
                    int(row["sample_count"]),
                    int(row["correct_count"]),
                    int(row["incorrect_count"]),
                    float(row["avg_confidence"]),
                    float(row["brier_score"]),
                    row["precision_high_conf"],
                    row["recall_high_conf"],
                    Json(row["metric_data"]),
                ),
            )

        for row in underperforming:
            await cur.execute(
                """
                INSERT INTO extraction_underperforming_classes (
                    period_key,
                    claim_class,
                    parser_version,
                    status,
                    brier_score,
                    precision_high_conf,
                    sample_count,
                    details,
                    computed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (period_key, claim_class, parser_version, status) DO UPDATE SET
                    brier_score = EXCLUDED.brier_score,
                    precision_high_conf = EXCLUDED.precision_high_conf,
                    sample_count = EXCLUDED.sample_count,
                    details = EXCLUDED.details,
                    computed_at = NOW()
                """,
                (
                    row["period_key"],
                    row["claim_class"],
                    row["parser_version"],
                    row["status"],
                    float(row["brier_score"]),
                    row["precision_high_conf"],
                    int(row["sample_count"]),
                    Json(row["details"]),
                ),
            )

    summary = {
        "status": "success",
        "window_days": settings.window_days,
        "total_claims": len(claim_rows),
        "considered_claims": len(records),
        "metrics_written": len(metrics),
        "underperforming_written": len(underperforming),
    }
    await _record_run(
        conn,
        status="success",
        settings=settings,
        total_claims=len(claim_rows),
        considered_claims=len(records),
        metrics_written=len(metrics),
        underperforming_written=len(underperforming),
        details=summary,
        started_at=started_at,
    )
    logger.info(
        "Refreshed extraction calibration: claims=%d considered=%d metrics=%d underperforming=%d",
        summary["total_claims"],
        summary["considered_claims"],
        summary["metrics_written"],
        summary["underperforming_written"],
    )
    return summary


async def resolve_extraction_calibration_status(
    conn: psycopg.AsyncConnection[Any],
) -> dict[str, Any]:
    """Resolve global calibration status for policy gating."""
    if not await _table_exists(conn, "extraction_calibration_metrics"):
        return {
            "status": "healthy",
            "reason": "calibration_metrics_unavailable",
            "period_key": None,
            "underperforming_classes": [],
        }

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT period_key
            FROM extraction_calibration_metrics
            WHERE period_granularity = 'week'
            ORDER BY period_key DESC
            LIMIT 1
            """
        )
        period_row = await cur.fetchone()

    if period_row is None:
        return {
            "status": "healthy",
            "reason": "no_calibration_data",
            "period_key": None,
            "underperforming_classes": [],
        }

    period_key = str(period_row["period_key"])
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT claim_class,
                   parser_version,
                   status,
                   drift_status,
                   brier_score,
                   precision_high_conf,
                   sample_count
            FROM extraction_calibration_metrics
            WHERE period_granularity = 'week'
              AND period_key = %s
            ORDER BY brier_score DESC, claim_class ASC, parser_version ASC
            """,
            (period_key,),
        )
        rows = await cur.fetchall()

    degraded = [row for row in rows if str(row["status"]) == "degraded"]
    monitor = [row for row in rows if str(row["status"]) == "monitor"]
    drift_alert = [row for row in rows if str(row["drift_status"]) == "drift_alert"]

    status = "healthy"
    if degraded:
        status = "degraded"
    elif monitor or drift_alert:
        status = "monitor"

    underperforming_classes = [
        {
            "claim_class": str(row["claim_class"]),
            "parser_version": str(row["parser_version"]),
            "status": str(row["status"]),
            "drift_status": str(row["drift_status"]),
            "brier_score": float(row["brier_score"]),
            "precision_high_conf": row["precision_high_conf"],
            "sample_count": int(row["sample_count"]),
        }
        for row in rows
        if str(row["status"]) != "healthy" or str(row["drift_status"]) == "drift_alert"
    ][:5]

    return {
        "status": status,
        "period_key": period_key,
        "classes_total": len(rows),
        "degraded_count": len(degraded),
        "monitor_count": len(monitor),
        "drift_alert_count": len(drift_alert),
        "underperforming_classes": underperforming_classes,
    }
