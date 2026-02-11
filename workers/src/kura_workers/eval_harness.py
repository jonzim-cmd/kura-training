"""Offline replay harness for inference calibration checks.

Supports replay from:
- projection histories (current projection artifacts)
- raw event store reconstruction

Optionally persists versioned run artifacts.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from .embeddings import cosine_similarity, get_embedding_provider
from .inference_engine import run_readiness_inference, run_strength_inference
from .utils import (
    epley_1rm,
    get_alias_map,
    get_retracted_event_ids,
    resolve_exercise_key,
    resolve_through_aliases,
)

SUPPORTED_PROJECTION_TYPES = ("semantic_memory", "strength_inference", "readiness_inference")
EVAL_SOURCE_PROJECTION_HISTORY = "projection_history"
EVAL_SOURCE_EVENT_STORE = "event_store"
EVAL_SOURCE_BOTH = "both"
_SUPPORTED_SOURCES = (
    EVAL_SOURCE_PROJECTION_HISTORY,
    EVAL_SOURCE_EVENT_STORE,
    EVAL_SOURCE_BOTH,
)

EVAL_STATUS_OK = "ok"
EVAL_STATUS_PARTIAL = "partial"
EVAL_STATUS_FAILED = "failed"

SEMANTIC_HIGH_CONFIDENCE_MIN = 0.86
SEMANTIC_MEDIUM_CONFIDENCE_MIN = 0.78
SEMANTIC_DEFAULT_TOP_K = 5

logger = logging.getLogger(__name__)


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def _ci_bounds(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, list) or len(value) != 2:
        return None
    low = _as_float(value[0])
    high = _as_float(value[1])
    if low is None or high is None:
        return None
    if low > high:
        low, high = high, low
    return low, high


def _round_or_none(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _safe_mean(values: Iterable[float]) -> float | None:
    items = list(values)
    if not items:
        return None
    return sum(items) / len(items)


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _mae(errors: list[float]) -> float | None:
    if not errors:
        return None
    return sum(abs(e) for e in errors) / len(errors)


def _rmse(errors: list[float]) -> float | None:
    if not errors:
        return None
    return (sum((e * e) for e in errors) / len(errors)) ** 0.5


def _read_strength_series(history: Any) -> list[tuple[date, float]]:
    if not isinstance(history, list):
        return []
    parsed: list[tuple[date, float]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        d = _parse_date(item.get("date"))
        e1rm = _as_float(item.get("estimated_1rm"))
        if d is None or e1rm is None:
            continue
        parsed.append((d, e1rm))
    parsed.sort(key=lambda x: x[0])
    return parsed


def _read_readiness_series(daily_scores: Any) -> list[tuple[date, float]]:
    if not isinstance(daily_scores, list):
        return []
    parsed: list[tuple[date, float]] = []
    for item in daily_scores:
        if not isinstance(item, dict):
            continue
        d = _parse_date(item.get("date"))
        score = _as_float(item.get("score"))
        if d is None or score is None:
            continue
        parsed.append((d, score))
    parsed.sort(key=lambda x: x[0])
    return parsed


def _normalize_term(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().lower().split())


def _confidence_band(score: float) -> str:
    if score >= SEMANTIC_HIGH_CONFIDENCE_MIN:
        return "high"
    if score >= SEMANTIC_MEDIUM_CONFIDENCE_MIN:
        return "medium"
    return "low"


def _parse_embedding(value: Any) -> list[float]:
    if not isinstance(value, list):
        return []
    out: list[float] = []
    for item in value:
        number = _as_float(item)
        if number is None:
            return []
        out.append(number)
    return out


def _normalize_projection_types(projection_types: list[str] | None) -> list[str]:
    selected = projection_types or list(SUPPORTED_PROJECTION_TYPES)
    selected = [s for s in selected if s in SUPPORTED_PROJECTION_TYPES]
    if not selected:
        return list(SUPPORTED_PROJECTION_TYPES)
    return selected


def _normalize_source(source: str | None) -> str:
    normalized = (source or EVAL_SOURCE_PROJECTION_HISTORY).strip().lower()
    if normalized not in _SUPPORTED_SOURCES:
        raise ValueError(
            f"Unsupported eval source: {normalized!r}. "
            f"Expected one of {', '.join(_SUPPORTED_SOURCES)}."
        )
    return normalized


@contextmanager
def _temporary_env(name: str, value: str | None):
    previous = os.environ.get(name)
    try:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def evaluate_strength_history(
    key: str,
    history: Any,
    *,
    strength_engine: str = "closed_form",
) -> dict[str, Any]:
    series = _read_strength_series(history)
    if len(series) < 3:
        return {
            "projection_type": "strength_inference",
            "key": key,
            "status": "insufficient_data",
            "series_points": len(series),
            "replay_windows": 0,
            "labeled_windows": 0,
            "metrics": {
                "coverage_ci95": None,
                "mae": None,
                "rmse": None,
                "mean_error": None,
                "plateau_brier": None,
                "velocity_mae": None,
                "direction_accuracy": None,
            },
        }

    start = series[0][0]
    model_points = [((d - start).days, v) for d, v in series]

    replay_windows = 0
    labeled_windows = 0
    inside_ci = 0
    errors: list[float] = []
    plateau_brier_terms: list[float] = []
    velocity_errors: list[float] = []
    direction_hits = 0
    direction_total = 0
    engines_used: dict[str, int] = {}
    horizons_seen: list[int] = []

    plateau_threshold = float(os.environ.get("KURA_BAYES_PLATEAU_SLOPE_PER_DAY", "0.02"))
    derivative_direction_eps = float(os.environ.get("KURA_STRENGTH_DERIVATIVE_VELOCITY_EPS", "0.03"))

    with _temporary_env("KURA_BAYES_ENGINE", strength_engine):
        for i in range(2, len(model_points)):
            train_points = model_points[: i + 1]
            inference = run_strength_inference(train_points)
            if inference.get("status") == "insufficient_data":
                continue

            replay_windows += 1
            engine = str(inference.get("engine", "none"))
            engines_used[engine] = engines_used.get(engine, 0) + 1

            predicted = inference.get("predicted_1rm") or {}
            pred_mean = _as_float(predicted.get("mean"))
            pred_ci = _ci_bounds(predicted.get("ci95"))
            horizon_days = int(predicted.get("horizon_days") or 0)
            if horizon_days <= 0:
                continue
            horizons_seen.append(horizon_days)

            current_date = series[i][0]
            current_e1rm = series[i][1]
            target_date = current_date + timedelta(days=horizon_days)

            actual_future: float | None = None
            for future_date, future_value in series[i + 1 :]:
                if future_date >= target_date:
                    actual_future = future_value
                    break
            if actual_future is None or pred_mean is None or pred_ci is None:
                continue

            labeled_windows += 1
            error = pred_mean - actual_future
            errors.append(error)

            if pred_ci[0] <= actual_future <= pred_ci[1]:
                inside_ci += 1

            plateau_probability = _as_float((inference.get("trend") or {}).get("plateau_probability"))
            if plateau_probability is not None:
                realized_slope = (actual_future - current_e1rm) / float(horizon_days)
                plateau_label = 1.0 if realized_slope <= plateau_threshold else 0.0
                plateau_brier_terms.append((plateau_probability - plateau_label) ** 2)

            realized_slope = (actual_future - current_e1rm) / float(horizon_days)
            dynamics = inference.get("dynamics") or {}
            predicted_velocity = _as_float(
                dynamics.get("model_velocity_per_day", dynamics.get("velocity_per_day"))
            )
            if predicted_velocity is not None:
                velocity_errors.append(predicted_velocity - realized_slope)

            predicted_direction = str(dynamics.get("direction") or "")
            if predicted_direction in {"up", "flat", "down"}:
                direction_total += 1
                if realized_slope > derivative_direction_eps:
                    realized_direction = "up"
                elif realized_slope < -derivative_direction_eps:
                    realized_direction = "down"
                else:
                    realized_direction = "flat"
                if predicted_direction == realized_direction:
                    direction_hits += 1

    metrics = {
        "coverage_ci95": _round_or_none(_safe_ratio(inside_ci, labeled_windows), 6),
        "mae": _round_or_none(_mae(errors), 6),
        "rmse": _round_or_none(_rmse(errors), 6),
        "mean_error": _round_or_none(_safe_mean(errors), 6),
        "plateau_brier": _round_or_none(_safe_mean(plateau_brier_terms), 6),
        "velocity_mae": _round_or_none(_mae(velocity_errors), 6),
        "direction_accuracy": _round_or_none(_safe_ratio(direction_hits, direction_total), 6),
    }
    status = "ok" if labeled_windows > 0 else "insufficient_labels"
    return {
        "projection_type": "strength_inference",
        "key": key,
        "status": status,
        "series_points": len(series),
        "replay_windows": replay_windows,
        "labeled_windows": labeled_windows,
        "engines_used": engines_used,
        "horizon_days_seen": sorted(set(horizons_seen)),
        "metrics": metrics,
    }


def _state_from_score(score: float) -> str:
    if score >= 0.72:
        return "high"
    if score <= 0.45:
        return "low"
    return "moderate"


def evaluate_readiness_daily_scores(key: str, daily_scores: Any) -> dict[str, Any]:
    series = _read_readiness_series(daily_scores)
    if len(series) < 5:
        return {
            "projection_type": "readiness_inference",
            "key": key,
            "status": "insufficient_data",
            "series_points": len(series),
            "replay_windows": 0,
            "labeled_windows": 0,
            "metrics": {
                "coverage_ci95_nowcast": None,
                "mae_nowcast": None,
                "rmse_nowcast": None,
                "state_accuracy": None,
                "velocity_mae_nowcast": None,
                "direction_accuracy_nowcast": None,
            },
        }

    observations = [v for _, v in series]
    replay_windows = 0
    labeled_windows = 0
    inside_ci = 0
    errors: list[float] = []
    state_hits = 0
    velocity_errors: list[float] = []
    direction_hits = 0
    direction_total = 0
    derivative_direction_eps = float(os.environ.get("KURA_READINESS_DERIVATIVE_VELOCITY_EPS", "0.015"))

    for i in range(4, len(observations)):
        subset = observations[: i + 1]
        inference = run_readiness_inference(subset)
        if inference.get("status") == "insufficient_data":
            continue
        replay_windows += 1

        readiness_today = inference.get("readiness_today") or {}
        pred_mean = _as_float(readiness_today.get("mean"))
        pred_ci = _ci_bounds(readiness_today.get("ci95"))
        pred_state = readiness_today.get("state")
        actual = observations[i]

        if pred_mean is None or pred_ci is None:
            continue

        labeled_windows += 1
        errors.append(pred_mean - actual)
        if pred_ci[0] <= actual <= pred_ci[1]:
            inside_ci += 1

        if pred_state in {"low", "moderate", "high"} and pred_state == _state_from_score(actual):
            state_hits += 1

        dynamics = inference.get("dynamics") or {}
        predicted_velocity = _as_float(dynamics.get("velocity_per_day"))
        if predicted_velocity is not None and i > 0:
            actual_velocity = observations[i] - observations[i - 1]
            velocity_errors.append(predicted_velocity - actual_velocity)

            predicted_direction = str(dynamics.get("direction") or "")
            if predicted_direction in {"up", "flat", "down"}:
                direction_total += 1
                if actual_velocity > derivative_direction_eps:
                    realized_direction = "up"
                elif actual_velocity < -derivative_direction_eps:
                    realized_direction = "down"
                else:
                    realized_direction = "flat"
                if predicted_direction == realized_direction:
                    direction_hits += 1

    metrics = {
        "coverage_ci95_nowcast": _round_or_none(_safe_ratio(inside_ci, labeled_windows), 6),
        "mae_nowcast": _round_or_none(_mae(errors), 6),
        "rmse_nowcast": _round_or_none(_rmse(errors), 6),
        "state_accuracy": _round_or_none(_safe_ratio(state_hits, labeled_windows), 6),
        "velocity_mae_nowcast": _round_or_none(_mae(velocity_errors), 6),
        "direction_accuracy_nowcast": _round_or_none(_safe_ratio(direction_hits, direction_total), 6),
    }
    status = "ok" if labeled_windows > 0 else "insufficient_labels"
    return {
        "projection_type": "readiness_inference",
        "key": key,
        "status": status,
        "series_points": len(series),
        "replay_windows": replay_windows,
        "labeled_windows": labeled_windows,
        "engines_used": {"normal_normal": replay_windows} if replay_windows > 0 else {},
        "metrics": metrics,
    }


def build_semantic_labels_from_event_rows(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Build term -> canonical labels from user-confirmed exercise mappings."""
    term_targets: dict[str, Counter[str]] = {}

    for row in rows:
        event_type = str(row.get("event_type") or "")
        data = row.get("data") or {}
        if not isinstance(data, dict):
            continue

        term = ""
        target = ""
        if event_type == "exercise.alias_created":
            term = _normalize_term(data.get("alias"))
            target = _normalize_term(data.get("exercise_id"))
        elif event_type == "set.logged":
            term = _normalize_term(data.get("exercise"))
            target = _normalize_term(data.get("exercise_id"))
        if not term or not target:
            continue

        bucket = term_targets.setdefault(term, Counter())
        bucket[target] += 1

    labels: dict[str, str] = {}
    for term, targets in term_targets.items():
        winner = targets.most_common(1)
        if winner:
            labels[term] = winner[0][0]
    return labels


def _evaluate_semantic_predictions(
    *,
    key: str,
    labels: dict[str, str],
    predictions: dict[str, list[tuple[str, float]]],
    top_k: int,
) -> dict[str, Any]:
    if not labels:
        return {
            "projection_type": "semantic_memory",
            "key": key,
            "status": "insufficient_data",
            "series_points": 0,
            "replay_windows": 0,
            "labeled_windows": 0,
            "metrics": {
                "coverage": None,
                "top1_accuracy": None,
                "topk_recall": None,
                "mrr": None,
                "top1_brier": None,
            },
            "confidence_calibration": {},
        }

    covered = 0
    top1_hits = 0
    topk_hits = 0
    mrr_sum = 0.0
    brier_terms: list[float] = []
    confidence_band_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    confidence_band_hits: dict[str, int] = {"high": 0, "medium": 0, "low": 0}

    for term, target in labels.items():
        ranked = predictions.get(term) or []
        if not ranked:
            continue
        covered += 1

        top_key, top_score = ranked[0]
        band = _confidence_band(top_score)
        confidence_band_counts[band] += 1

        if top_key == target:
            top1_hits += 1
            confidence_band_hits[band] += 1
            brier_terms.append((top_score - 1.0) ** 2)
        else:
            brier_terms.append((top_score - 0.0) ** 2)

        rank_of_target: int | None = None
        for rank, (candidate_key, _) in enumerate(ranked[:top_k], start=1):
            if candidate_key == target:
                rank_of_target = rank
                break
        if rank_of_target is not None:
            topk_hits += 1
            mrr_sum += 1.0 / rank_of_target

    labeled = len(labels)
    calibration: dict[str, dict[str, float | int | None]] = {}
    for band in ("high", "medium", "low"):
        total = confidence_band_counts[band]
        hits = confidence_band_hits[band]
        calibration[band] = {
            "count": total,
            "precision": _round_or_none(_safe_ratio(hits, total), 6),
        }

    metrics = {
        "coverage": _round_or_none(_safe_ratio(covered, labeled), 6),
        "top1_accuracy": _round_or_none(_safe_ratio(top1_hits, labeled), 6),
        "topk_recall": _round_or_none(_safe_ratio(topk_hits, labeled), 6),
        "mrr": _round_or_none((mrr_sum / labeled) if labeled else None, 6),
        "top1_brier": _round_or_none(_safe_mean(brier_terms), 6),
    }
    status = "ok" if covered > 0 else "insufficient_labels"
    return {
        "projection_type": "semantic_memory",
        "key": key,
        "status": status,
        "series_points": covered,
        "replay_windows": covered,
        "labeled_windows": labeled,
        "metrics": metrics,
        "confidence_calibration": calibration,
    }


def evaluate_semantic_memory_projection_labels(
    key: str,
    projection_data: Any,
    labels: dict[str, str],
    *,
    top_k: int = SEMANTIC_DEFAULT_TOP_K,
) -> dict[str, Any]:
    data = projection_data if isinstance(projection_data, dict) else {}
    candidates = data.get("exercise_candidates")
    predictions: dict[str, list[tuple[str, float]]] = {}
    if isinstance(candidates, list):
        for item in candidates:
            if not isinstance(item, dict):
                continue
            term = _normalize_term(item.get("term"))
            canonical = _normalize_term(item.get("suggested_exercise_id"))
            score = _as_float(item.get("score"))
            if not term or not canonical or score is None:
                continue
            predictions[term] = [(canonical, score)]
    result = _evaluate_semantic_predictions(
        key=key,
        labels=labels,
        predictions=predictions,
        top_k=max(1, int(top_k)),
    )
    result["top_k"] = max(1, int(top_k))
    return result


def evaluate_semantic_event_store_labels(
    labels: dict[str, str],
    catalog_embeddings: list[dict[str, Any]],
    *,
    top_k: int = SEMANTIC_DEFAULT_TOP_K,
) -> dict[str, Any]:
    if not labels:
        return _evaluate_semantic_predictions(
            key="overview",
            labels={},
            predictions={},
            top_k=max(1, int(top_k)),
        )

    provider = get_embedding_provider()
    terms = sorted(labels.keys())
    vectors = provider.embed_many(terms)
    predictions: dict[str, list[tuple[str, float]]] = {}
    rank_limit = max(1, int(top_k))

    for term, term_vec in zip(terms, vectors):
        scored: list[tuple[str, float]] = []
        for catalog in catalog_embeddings:
            score = cosine_similarity(term_vec, catalog["embedding"])
            if score <= 0.0:
                continue
            scored.append((catalog["canonical_key"], score))
        scored.sort(key=lambda x: x[1], reverse=True)
        predictions[term] = scored[:rank_limit]

    result = _evaluate_semantic_predictions(
        key="overview",
        labels=labels,
        predictions=predictions,
        top_k=rank_limit,
    )
    result["top_k"] = rank_limit
    return result


def _median(values: list[float]) -> float:
    if not values:
        return 1.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _clamp(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))


def filter_retracted_event_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop event.retracted records and the events they retract."""
    retracted_ids: set[str] = set()
    for row in rows:
        if row.get("event_type") != "event.retracted":
            continue
        data = row.get("data") or {}
        retracted_id = str(data.get("retracted_event_id") or "").strip()
        if retracted_id:
            retracted_ids.add(retracted_id)

    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("event_type") == "event.retracted":
            continue
        row_id = str(row.get("id") or "")
        if row_id and row_id in retracted_ids:
            continue
        out.append(row)
    return out


def build_strength_histories_from_event_rows(
    rows: list[dict[str, Any]],
    alias_map: dict[str, str],
) -> dict[str, list[dict[str, Any]]]:
    """Reconstruct strength history per canonical exercise from raw events."""
    session_best: dict[str, dict[str, tuple[datetime, float]]] = {}
    day_best: dict[str, dict[str, float]] = {}

    for row in rows:
        if row.get("event_type") != "set.logged":
            continue
        data = row.get("data") or {}
        raw_key = resolve_exercise_key(data)
        if not raw_key:
            continue
        canonical = resolve_through_aliases(raw_key, alias_map)
        if not canonical:
            continue

        try:
            weight = float(data.get("weight_kg", data.get("weight", 0)))
            reps = int(data.get("reps", 0))
        except (ValueError, TypeError):
            continue

        e1rm = epley_1rm(weight, reps)
        if e1rm <= 0:
            continue

        ts = row.get("timestamp")
        if not isinstance(ts, datetime):
            continue
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        session_id = str(metadata.get("session_id") or ts.date().isoformat())
        day_key = ts.date().isoformat()

        per_ex_session = session_best.setdefault(canonical, {})
        prev = per_ex_session.get(session_id)
        if prev is None or e1rm > prev[1]:
            per_ex_session[session_id] = (ts, e1rm)

        per_ex_day = day_best.setdefault(canonical, {})
        prev_day = per_ex_day.get(day_key, 0.0)
        if e1rm > prev_day:
            per_ex_day[day_key] = e1rm

    out: dict[str, list[dict[str, Any]]] = {}
    for canonical, by_day in day_best.items():
        history = [
            {"date": d, "estimated_1rm": round(v, 2)}
            for d, v in sorted(by_day.items())
        ]
        if history:
            out[canonical] = history
    return out


def build_readiness_daily_scores_from_event_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct readiness daily_scores from raw events."""
    per_day: dict[str, dict[str, Any]] = {}
    load_values: list[float] = []

    for row in rows:
        event_type = row.get("event_type")
        if event_type not in {"set.logged", "sleep.logged", "soreness.logged", "energy.logged"}:
            continue
        ts = row.get("timestamp")
        if not isinstance(ts, datetime):
            continue
        day = ts.date().isoformat()
        bucket = per_day.setdefault(day, {})
        data = row.get("data") or {}

        if event_type == "sleep.logged":
            try:
                bucket["sleep_hours"] = float(data.get("duration_hours"))
            except (TypeError, ValueError):
                pass
        elif event_type == "energy.logged":
            try:
                bucket["energy"] = float(data.get("level"))
            except (TypeError, ValueError):
                pass
        elif event_type == "soreness.logged":
            try:
                sev = float(data.get("severity"))
            except (TypeError, ValueError):
                continue
            bucket["soreness_sum"] = bucket.get("soreness_sum", 0.0) + sev
            bucket["soreness_count"] = bucket.get("soreness_count", 0) + 1
        elif event_type == "set.logged":
            try:
                weight = float(data.get("weight_kg", data.get("weight", 0)))
                reps = float(data.get("reps", 0))
            except (TypeError, ValueError):
                continue
            volume = max(0.0, weight * reps)
            bucket["load_volume"] = bucket.get("load_volume", 0.0) + volume

    for values in per_day.values():
        load = float(values.get("load_volume", 0.0))
        if load > 0.0:
            load_values.append(load)
    load_baseline = max(1.0, _median(load_values))

    daily_scores: list[dict[str, Any]] = []
    for day in sorted(per_day):
        values = per_day[day]
        has_any = any(
            key in values for key in ("sleep_hours", "energy", "soreness_sum", "load_volume")
        )
        if not has_any:
            continue

        sleep_score = _clamp(float(values.get("sleep_hours", 6.5)) / 8.0, 0.0, 1.2)
        energy_score = _clamp(float(values.get("energy", 6.0)) / 10.0, 0.0, 1.0)
        soreness_avg = 0.0
        if values.get("soreness_count", 0):
            soreness_avg = float(values.get("soreness_sum", 0.0)) / float(values["soreness_count"])
        soreness_penalty = _clamp(soreness_avg / 5.0, 0.0, 1.0)

        load = float(values.get("load_volume", 0.0))
        load_penalty = _clamp(load / load_baseline, 0.0, 1.4)

        score = (
            0.45 * sleep_score
            + 0.35 * energy_score
            - 0.20 * soreness_penalty
            - 0.15 * load_penalty
            + 0.25
        )
        score = _clamp(score, 0.0, 1.0)

        daily_scores.append(
            {
                "date": day,
                "score": round(score, 3),
                "components": {
                    "sleep": round(sleep_score, 3),
                    "energy": round(energy_score, 3),
                    "soreness_penalty": round(soreness_penalty, 3),
                    "load_penalty": round(load_penalty, 3),
                },
            }
        )

    return daily_scores


def evaluate_from_event_store_rows(
    rows: list[dict[str, Any]],
    *,
    projection_types: list[str] | None = None,
    strength_engine: str = "closed_form",
    semantic_top_k: int = SEMANTIC_DEFAULT_TOP_K,
    catalog_embeddings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Run replay evaluation directly from raw event rows."""
    selected = _normalize_projection_types(projection_types)
    active_rows = filter_retracted_event_rows(rows)
    labels = build_semantic_labels_from_event_rows(active_rows)
    alias_map: dict[str, str] = {}
    for row in active_rows:
        if row.get("event_type") != "exercise.alias_created":
            continue
        data = row.get("data") or {}
        alias = str(data.get("alias", "")).strip().lower()
        target = str(data.get("exercise_id", "")).strip().lower()
        if alias and target:
            alias_map[alias] = target

    results: list[dict[str, Any]] = []
    if "strength_inference" in selected:
        histories = build_strength_histories_from_event_rows(active_rows, alias_map)
        for key in sorted(histories):
            result = evaluate_strength_history(
                key,
                histories[key],
                strength_engine=strength_engine,
            )
            result["source"] = EVAL_SOURCE_EVENT_STORE
            results.append(result)

    if "readiness_inference" in selected:
        readiness_daily = build_readiness_daily_scores_from_event_rows(active_rows)
        result = evaluate_readiness_daily_scores("overview", readiness_daily)
        result["source"] = EVAL_SOURCE_EVENT_STORE
        results.append(result)

    if "semantic_memory" in selected:
        semantic_catalog = catalog_embeddings or []
        result = evaluate_semantic_event_store_labels(
            labels,
            semantic_catalog,
            top_k=semantic_top_k,
        )
        result["source"] = EVAL_SOURCE_EVENT_STORE
        results.append(result)

    return results


def summarize_projection_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        ptype = str(row.get("projection_type") or "unknown")
        by_type.setdefault(ptype, []).append(row)

    summary: dict[str, Any] = {}
    for ptype, rows in by_type.items():
        total_replay = sum(int(r.get("replay_windows", 0) or 0) for r in rows)
        total_labeled = sum(int(r.get("labeled_windows", 0) or 0) for r in rows)
        ok_count = sum(1 for r in rows if r.get("status") == "ok")
        summary[ptype] = {
            "projection_rows": len(rows),
            "ok_rows": ok_count,
            "replay_windows": total_replay,
            "labeled_windows": total_labeled,
        }
    return summary


def summarize_projection_results_by_source(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, dict[str, Any]] = {}
    for row in results:
        source = str(row.get("source") or "unknown")
        ptype = str(row.get("projection_type") or "unknown")
        source_bucket = by_source.setdefault(
            source,
            {
                "projection_rows": 0,
                "ok_rows": 0,
                "replay_windows": 0,
                "labeled_windows": 0,
                "by_projection_type": {},
            },
        )
        source_bucket["projection_rows"] += 1
        if row.get("status") == "ok":
            source_bucket["ok_rows"] += 1
        source_bucket["replay_windows"] += int(row.get("replay_windows", 0) or 0)
        source_bucket["labeled_windows"] += int(row.get("labeled_windows", 0) or 0)

        by_type = source_bucket["by_projection_type"].setdefault(
            ptype,
            {"projection_rows": 0, "ok_rows": 0},
        )
        by_type["projection_rows"] += 1
        if row.get("status") == "ok":
            by_type["ok_rows"] += 1
    return by_source


def _metric_threshold(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _metric_values_for_source(
    results: list[dict[str, Any]],
    *,
    projection_type: str,
    metric_name: str,
    source: str,
) -> list[float]:
    out: list[float] = []
    for row in results:
        if row.get("projection_type") != projection_type:
            continue
        if row.get("source") != source:
            continue
        if row.get("status") != "ok":
            continue
        metrics = row.get("metrics")
        if not isinstance(metrics, dict):
            continue
        value = _as_float(metrics.get(metric_name))
        if value is None:
            continue
        out.append(value)
    return out


def _compare_metric(value: float, comparator: str, threshold: float) -> bool:
    if comparator == "ge":
        return value >= threshold
    if comparator == "le":
        return value <= threshold
    raise ValueError(f"Unsupported comparator: {comparator!r}")


def build_shadow_mode_rollout_checks(
    results: list[dict[str, Any]],
    *,
    source_mode: str,
) -> dict[str, Any]:
    threshold_specs = [
        ("strength_inference", "coverage_ci95", "ge", _metric_threshold("KURA_SHADOW_STRENGTH_COVERAGE_MIN", 0.7)),
        ("strength_inference", "mae", "le", _metric_threshold("KURA_SHADOW_STRENGTH_MAE_MAX", 15.0)),
        ("readiness_inference", "coverage_ci95_nowcast", "ge", _metric_threshold("KURA_SHADOW_READINESS_COVERAGE_MIN", 0.7)),
        ("readiness_inference", "mae_nowcast", "le", _metric_threshold("KURA_SHADOW_READINESS_MAE_MAX", 0.18)),
        ("semantic_memory", "top1_accuracy", "ge", _metric_threshold("KURA_SHADOW_SEMANTIC_TOP1_MIN", 0.6)),
        ("semantic_memory", "topk_recall", "ge", _metric_threshold("KURA_SHADOW_SEMANTIC_TOPK_MIN", 0.8)),
    ]

    source_order = [source_mode]
    if source_mode == EVAL_SOURCE_BOTH:
        source_order = [EVAL_SOURCE_EVENT_STORE, EVAL_SOURCE_PROJECTION_HISTORY]

    checks: list[dict[str, Any]] = []
    for projection_type, metric_name, comparator, threshold in threshold_specs:
        values: list[float] = []
        used_source: str | None = None
        for source in source_order:
            source_values = _metric_values_for_source(
                results,
                projection_type=projection_type,
                metric_name=metric_name,
                source=source,
            )
            if source_values:
                values = source_values
                used_source = source
                break

        mean_value = _safe_mean(values)
        passed = (
            _compare_metric(mean_value, comparator, threshold)
            if mean_value is not None
            else False
        )
        checks.append(
            {
                "projection_type": projection_type,
                "metric": metric_name,
                "source": used_source,
                "comparator": comparator,
                "threshold": threshold,
                "value": _round_or_none(mean_value, 6),
                "samples": len(values),
                "passed": passed,
            }
        )

    if checks and all(c["passed"] for c in checks):
        status = "pass"
    elif any(c["value"] is None for c in checks):
        status = "insufficient_data"
    else:
        status = "fail"

    return {
        "status": status,
        "allow_autonomous_behavior_changes": status == "pass",
        "checks": checks,
    }


def _eval_run_status(results: list[dict[str, Any]]) -> str:
    if not results:
        return EVAL_STATUS_FAILED
    statuses = [str(r.get("status", "")) for r in results]
    if statuses and all(s == "ok" for s in statuses):
        return EVAL_STATUS_OK
    if any(s == "ok" for s in statuses):
        return EVAL_STATUS_PARTIAL
    return EVAL_STATUS_FAILED


async def persist_eval_run(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    source: str,
    projection_types: list[str],
    strength_engine: str,
    summary: dict[str, Any],
    summary_by_source: dict[str, Any],
    shadow_mode: dict[str, Any],
    results: list[dict[str, Any]],
) -> str:
    persisted_source = "combined" if source == EVAL_SOURCE_BOTH else source
    status = "completed" if _eval_run_status(results) != EVAL_STATUS_FAILED else "failed"
    config = {
        "source": source,
        "projection_types": projection_types,
        "strength_engine": strength_engine,
    }

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            INSERT INTO inference_eval_runs (
                user_id, source, projection_types, strength_engine, status,
                config, summary
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                user_id,
                persisted_source,
                Json(projection_types),
                strength_engine,
                status,
                Json(config),
                Json(
                    {
                        "by_projection_type": summary,
                        "by_source": summary_by_source,
                        "shadow_mode": shadow_mode,
                    }
                ),
            ),
        )
        run_row = await cur.fetchone()
    run_id = str(run_row["id"])

    if results:
        async with conn.cursor() as cur:
            for result in results:
                projection_type = str(result.get("projection_type") or "unknown")
                projection_key = str(result.get("key") or "unknown")
                artifact_source = str(result.get("source") or "unknown")
                await cur.execute(
                    """
                    INSERT INTO inference_eval_artifacts (
                        run_id, user_id, source, projection_type, projection_key, artifact
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id,
                        user_id,
                        artifact_source,
                        projection_type,
                        projection_key,
                        Json(result),
                    ),
                )

    return run_id


async def _fetch_projection_rows(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    projection_types: list[str],
) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT projection_type, key, data, updated_at
            FROM projections
            WHERE user_id = %s
              AND projection_type = ANY(%s)
            ORDER BY projection_type ASC, key ASC
            """,
            (user_id, projection_types),
        )
        return await cur.fetchall()


async def _fetch_semantic_catalog_embeddings(
    conn: psycopg.AsyncConnection[Any],
    *,
    domain: str = "exercise",
) -> list[dict[str, Any]]:
    try:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT DISTINCT ON (c.canonical_key)
                    c.canonical_key,
                    c.canonical_label,
                    ce.embedding
                FROM semantic_catalog c
                JOIN semantic_catalog_embeddings ce ON ce.catalog_id = c.id
                WHERE c.domain = %s
                ORDER BY c.canonical_key, ce.updated_at DESC, ce.created_at DESC
                """,
                (domain,),
            )
            rows = await cur.fetchall()
    except Exception as exc:
        logger.warning("Semantic catalog embeddings unavailable for eval harness: %s", exc)
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        vec = _parse_embedding(row["embedding"])
        if not vec:
            continue
        out.append(
            {
                "canonical_key": str(row["canonical_key"]),
                "canonical_label": str(row["canonical_label"]),
                "embedding": vec,
            }
        )
    return out


async def _fetch_active_semantic_label_rows(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
) -> list[dict[str, Any]]:
    event_types = ("set.logged", "exercise.alias_created", "event.retracted")
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, event_type, data, metadata
            FROM events
            WHERE user_id = %s
              AND event_type = ANY(%s)
            ORDER BY timestamp ASC, id ASC
            """,
            (user_id, event_types),
        )
        rows = await cur.fetchall()

    retracted_ids = await get_retracted_event_ids(conn, user_id)
    filtered_rows = [r for r in rows if str(r["id"]) not in retracted_ids]
    return filter_retracted_event_rows(filtered_rows)


async def _projection_history_results(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    projection_types: list[str],
    strength_engine: str,
    semantic_labels: dict[str, str] | None = None,
    semantic_top_k: int = SEMANTIC_DEFAULT_TOP_K,
) -> list[dict[str, Any]]:
    rows = await _fetch_projection_rows(conn, user_id=user_id, projection_types=projection_types)
    results: list[dict[str, Any]] = []
    for row in rows:
        projection_type = row["projection_type"]
        key = row["key"]
        data = row["data"] or {}
        if projection_type == "strength_inference":
            eval_result = evaluate_strength_history(
                key,
                data.get("history"),
                strength_engine=strength_engine,
            )
        elif projection_type == "readiness_inference":
            eval_result = evaluate_readiness_daily_scores(
                key,
                data.get("daily_scores"),
            )
        elif projection_type == "semantic_memory":
            eval_result = evaluate_semantic_memory_projection_labels(
                key,
                data,
                semantic_labels or {},
                top_k=semantic_top_k,
            )
        else:
            continue
        eval_result["updated_at"] = (
            row["updated_at"].isoformat()
            if hasattr(row["updated_at"], "isoformat")
            else str(row["updated_at"])
        )
        eval_result["source"] = EVAL_SOURCE_PROJECTION_HISTORY
        results.append(eval_result)
    return results


async def _event_store_results(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    projection_types: list[str],
    strength_engine: str,
    semantic_top_k: int = SEMANTIC_DEFAULT_TOP_K,
    semantic_catalog_embeddings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    event_types: set[str] = set()
    if "semantic_memory" in projection_types:
        event_types.update(("set.logged", "exercise.alias_created", "event.retracted"))
    if "strength_inference" in projection_types:
        event_types.update(("set.logged", "exercise.alias_created", "event.retracted"))
    if "readiness_inference" in projection_types:
        event_types.update(
            ("set.logged", "sleep.logged", "soreness.logged", "energy.logged", "event.retracted")
        )
    if not event_types:
        return []

    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT id, timestamp, event_type, data, metadata
            FROM events
            WHERE user_id = %s
              AND event_type = ANY(%s)
            ORDER BY timestamp ASC, id ASC
            """,
            (user_id, sorted(event_types)),
        )
        rows = await cur.fetchall()

    # DB-derived retraction set to mirror handler behavior.
    retracted_ids = await get_retracted_event_ids(conn, user_id)
    filtered_rows = [r for r in rows if str(r["id"]) not in retracted_ids]

    # Build alias map from active events for retroactive canonical resolution.
    alias_map = await get_alias_map(conn, user_id, retracted_ids=retracted_ids)
    active_rows = filter_retracted_event_rows(filtered_rows)

    results: list[dict[str, Any]] = []
    if "strength_inference" in projection_types:
        histories = build_strength_histories_from_event_rows(active_rows, alias_map)
        for key in sorted(histories):
            eval_result = evaluate_strength_history(
                key,
                histories[key],
                strength_engine=strength_engine,
            )
            eval_result["source"] = EVAL_SOURCE_EVENT_STORE
            results.append(eval_result)

    if "readiness_inference" in projection_types:
        readiness_daily = build_readiness_daily_scores_from_event_rows(active_rows)
        eval_result = evaluate_readiness_daily_scores("overview", readiness_daily)
        eval_result["source"] = EVAL_SOURCE_EVENT_STORE
        results.append(eval_result)

    if "semantic_memory" in projection_types:
        labels = build_semantic_labels_from_event_rows(active_rows)
        semantic_result = evaluate_semantic_event_store_labels(
            labels,
            semantic_catalog_embeddings or [],
            top_k=semantic_top_k,
        )
        semantic_result["source"] = EVAL_SOURCE_EVENT_STORE
        results.append(semantic_result)

    return results


async def run_eval_harness(
    conn: psycopg.AsyncConnection[Any],
    *,
    user_id: str,
    projection_types: list[str] | None = None,
    strength_engine: str = "closed_form",
    semantic_top_k: int = SEMANTIC_DEFAULT_TOP_K,
    source: str = EVAL_SOURCE_PROJECTION_HISTORY,
    persist: bool = False,
) -> dict[str, Any]:
    selected = _normalize_projection_types(projection_types)
    source_mode = _normalize_source(source)
    semantic_top_k = max(1, int(semantic_top_k))

    semantic_labels: dict[str, str] = {}
    semantic_catalog_embeddings: list[dict[str, Any]] = []
    if "semantic_memory" in selected:
        semantic_rows = await _fetch_active_semantic_label_rows(conn, user_id=user_id)
        semantic_labels = build_semantic_labels_from_event_rows(semantic_rows)
        if source_mode in {EVAL_SOURCE_EVENT_STORE, EVAL_SOURCE_BOTH}:
            semantic_catalog_embeddings = await _fetch_semantic_catalog_embeddings(
                conn,
                domain="exercise",
            )

    results: list[dict[str, Any]] = []
    if source_mode in {EVAL_SOURCE_PROJECTION_HISTORY, EVAL_SOURCE_BOTH}:
        results.extend(
            await _projection_history_results(
                conn,
                user_id=user_id,
                projection_types=selected,
                strength_engine=strength_engine,
                semantic_labels=semantic_labels,
                semantic_top_k=semantic_top_k,
            )
        )
    if source_mode in {EVAL_SOURCE_EVENT_STORE, EVAL_SOURCE_BOTH}:
        results.extend(
            await _event_store_results(
                conn,
                user_id=user_id,
                projection_types=selected,
                strength_engine=strength_engine,
                semantic_top_k=semantic_top_k,
                semantic_catalog_embeddings=semantic_catalog_embeddings,
            )
        )

    summary = summarize_projection_results(results)
    summary_by_source = summarize_projection_results_by_source(results)
    shadow_mode = build_shadow_mode_rollout_checks(results, source_mode=source_mode)
    output: dict[str, Any] = {
        "user_id": user_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "projection_types": selected,
        "source": source_mode,
        "strength_engine": strength_engine,
        "semantic_top_k": semantic_top_k,
        "projection_rows": len(results),
        "summary": summary,
        "summary_by_source": summary_by_source,
        "eval_status": _eval_run_status(results),
        "shadow_mode": shadow_mode,
        "results": results,
    }

    if persist:
        run_id = await persist_eval_run(
            conn,
            user_id=user_id,
            source=source_mode,
            projection_types=selected,
            strength_engine=strength_engine,
            summary=summary,
            summary_by_source=summary_by_source,
            shadow_mode=shadow_mode,
            results=results,
        )
        output["run_id"] = run_id

    return output
