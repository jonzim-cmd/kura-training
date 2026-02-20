from datetime import datetime, timedelta, timezone

from kura_workers.strength_benchmark import (
    build_strength_benchmark_rows,
    evaluate_strength_benchmark,
)


def _row(
    idx: int,
    *,
    ts: datetime,
    weight_kg: float,
    reps: int,
    rir: float | None = None,
    rpe: float | None = None,
    set_type: str | None = None,
) -> dict:
    data: dict[str, object] = {
        "exercise_id": "bench_press",
        "weight_kg": weight_kg,
        "reps": reps,
    }
    if rir is not None:
        data["rir"] = rir
    if rpe is not None:
        data["rpe"] = rpe
    if set_type is not None:
        data["set_type"] = set_type
    return {
        "id": f"evt-{idx}",
        "timestamp": ts,
        "data": data,
    }


def test_build_strength_benchmark_rows_links_to_future_anchor() -> None:
    start = datetime(2026, 2, 1, tzinfo=timezone.utc)
    rows = [
        _row(1, ts=start, weight_kg=70, reps=3, rir=3),
        _row(2, ts=start + timedelta(days=3), weight_kg=72.5, reps=3, rir=2),
        _row(3, ts=start + timedelta(days=5), weight_kg=85, reps=1, set_type="test_single"),
    ]
    benchmark_rows = build_strength_benchmark_rows(rows, anchor_window_days=14)
    assert benchmark_rows
    assert all(entry["exercise_id"] == "bench_press" for entry in benchmark_rows)
    assert all(entry["anchor_gap_days"] >= 0 for entry in benchmark_rows)


def test_evaluate_strength_benchmark_reports_metrics() -> None:
    start = datetime(2026, 2, 1, tzinfo=timezone.utc)
    rows: list[dict] = []
    for idx in range(14):
        ts = start + timedelta(days=idx * 3)
        rows.append(_row(idx + 1, ts=ts, weight_kg=70 + idx * 0.5, reps=3, rir=2))
        rows.append(
            _row(
                100 + idx,
                ts=ts + timedelta(days=1),
                weight_kg=82 + idx * 0.5,
                reps=1,
                set_type="test_single",
            )
        )

    benchmark_rows = build_strength_benchmark_rows(rows, anchor_window_days=7)
    result = evaluate_strength_benchmark(benchmark_rows, min_rows=12)
    assert result["status"] == "ok"
    assert result["observed_rows"] >= 12
    assert result["metrics"]["mae"] is not None
    assert result["metrics"]["coverage_within_5pct"] is not None
    assert "explicit" in result["by_source"] or "fallback_epley" in result["by_source"]
