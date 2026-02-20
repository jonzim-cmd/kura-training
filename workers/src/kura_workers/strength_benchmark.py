"""Benchmark extraction and calibration metrics for strength e1RM estimators."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from math import sqrt
from typing import Any

from .capability_estimation_runtime import effort_adjusted_e1rm
from .utils import resolve_exercise_key, resolve_through_aliases

_ANCHOR_SET_TYPES = {
    "test_single",
    "single_test",
    "one_rep_max_test",
    "max_attempt",
    "competition_single",
}


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _mae(errors: list[float]) -> float | None:
    if not errors:
        return None
    return sum(abs(e) for e in errors) / len(errors)


def _rmse(errors: list[float]) -> float | None:
    if not errors:
        return None
    return sqrt(sum((e * e) for e in errors) / len(errors))


def _is_anchor_set(data: dict[str, Any], reps: int) -> bool:
    if reps <= 2:
        return True
    set_type = str(data.get("set_type") or "").strip().lower().replace(" ", "_")
    return set_type in _ANCHOR_SET_TYPES


def build_strength_benchmark_rows(
    rows: list[dict[str, Any]],
    *,
    alias_map: dict[str, str] | None = None,
    anchor_window_days: int = 42,
) -> list[dict[str, Any]]:
    """Build deterministic benchmark rows linking candidate sets to anchor attempts."""
    alias_map = alias_map or {}
    by_exercise: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in sorted(rows, key=lambda item: (item.get("timestamp"), str(item.get("id") or ""))):
        data = row.get("data")
        ts = row.get("timestamp")
        if not isinstance(data, dict) or not isinstance(ts, datetime):
            continue
        key = resolve_exercise_key(data)
        if not key:
            continue
        canonical = resolve_through_aliases(key, alias_map)
        by_exercise[canonical].append(row)

    benchmark_rows: list[dict[str, Any]] = []
    max_anchor_gap = timedelta(days=max(1, int(anchor_window_days)))
    for exercise_id, exercise_rows in sorted(by_exercise.items(), key=lambda item: item[0]):
        anchors: list[tuple[datetime, float]] = []
        candidates: list[tuple[datetime, float, str, str]] = []
        for row in exercise_rows:
            data = row.get("data") or {}
            ts = row.get("timestamp")
            if not isinstance(ts, datetime):
                continue
            reps = _to_int(data.get("reps"))
            weight = _to_float(data.get("weight_kg", data.get("weight")))
            if reps is None or weight is None:
                continue
            e1rm, source = effort_adjusted_e1rm(
                weight,
                reps,
                rir=data.get("rir"),
                rpe=data.get("rpe"),
            )
            if e1rm <= 0:
                continue
            if _is_anchor_set(data, reps):
                anchors.append((ts, e1rm))
            candidates.append((ts, e1rm, source, str(row.get("id") or "")))

        if not anchors or not candidates:
            continue

        anchors.sort(key=lambda item: item[0])
        for ts, e1rm, source, event_id in candidates:
            future_anchors = [anchor for anchor in anchors if anchor[0] >= ts]
            if not future_anchors:
                continue
            anchor_ts, anchor_e1rm = future_anchors[0]
            if anchor_ts - ts > max_anchor_gap:
                continue
            error = e1rm - anchor_e1rm
            benchmark_rows.append(
                {
                    "exercise_id": exercise_id,
                    "candidate_event_id": event_id,
                    "candidate_timestamp": ts.isoformat(),
                    "anchor_timestamp": anchor_ts.isoformat(),
                    "anchor_gap_days": round((anchor_ts - ts).total_seconds() / 86400.0, 3),
                    "estimator_source": source,
                    "candidate_e1rm": round(e1rm, 4),
                    "anchor_e1rm": round(anchor_e1rm, 4),
                    "error": round(error, 4),
                    "abs_error": round(abs(error), 4),
                }
            )
    return benchmark_rows


def evaluate_strength_benchmark(
    benchmark_rows: list[dict[str, Any]],
    *,
    min_rows: int = 12,
) -> dict[str, Any]:
    """Compute benchmark metrics for estimator calibration and rollout gating."""
    if len(benchmark_rows) < min_rows:
        return {
            "status": "insufficient_data",
            "required_rows": int(min_rows),
            "observed_rows": int(len(benchmark_rows)),
            "metrics": {
                "mae": None,
                "rmse": None,
                "mean_error": None,
                "coverage_within_5pct": None,
            },
            "by_source": {},
        }

    errors = [float(row.get("error") or 0.0) for row in benchmark_rows]
    coverage_hits = 0
    for row in benchmark_rows:
        anchor = float(row.get("anchor_e1rm") or 0.0)
        error = abs(float(row.get("error") or 0.0))
        if anchor > 0 and (error / anchor) <= 0.05:
            coverage_hits += 1

    by_source_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in benchmark_rows:
        by_source_rows[str(row.get("estimator_source") or "unknown")].append(row)
    by_source: dict[str, dict[str, Any]] = {}
    for source, rows in sorted(by_source_rows.items(), key=lambda item: item[0]):
        source_errors = [float(item.get("error") or 0.0) for item in rows]
        by_source[source] = {
            "rows": len(rows),
            "mae": round(_mae(source_errors) or 0.0, 6),
            "rmse": round(_rmse(source_errors) or 0.0, 6),
            "mean_error": round(_mean(source_errors) or 0.0, 6),
        }

    return {
        "status": "ok",
        "required_rows": int(min_rows),
        "observed_rows": int(len(benchmark_rows)),
        "metrics": {
            "mae": round(_mae(errors) or 0.0, 6),
            "rmse": round(_rmse(errors) or 0.0, 6),
            "mean_error": round(_mean(errors) or 0.0, 6),
            "coverage_within_5pct": round(coverage_hits / len(benchmark_rows), 6),
        },
        "by_source": by_source,
    }
