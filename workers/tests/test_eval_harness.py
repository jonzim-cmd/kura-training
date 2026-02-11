"""Unit tests for offline replay evaluation harness."""

from datetime import date, datetime, timedelta, timezone

from kura_workers.eval_harness import (
    build_semantic_labels_from_event_rows,
    build_shadow_mode_rollout_checks,
    evaluate_semantic_event_store_labels,
    evaluate_semantic_memory_projection_labels,
    evaluate_from_event_store_rows,
    evaluate_readiness_daily_scores,
    evaluate_strength_history,
    filter_retracted_event_rows,
    summarize_projection_results,
    summarize_projection_results_by_source,
)


def _strength_history(values):
    base = date(2026, 1, 1)
    out = []
    for idx, value in enumerate(values):
        d = base + timedelta(days=idx * 7)
        out.append({"date": d.isoformat(), "estimated_1rm": value})
    return out


def _readiness_daily(values):
    base = date(2026, 2, 1)
    out = []
    for idx, value in enumerate(values):
        d = base + timedelta(days=idx)
        out.append({"date": d.isoformat(), "score": value})
    return out


def test_evaluate_strength_history_with_labels(monkeypatch):
    monkeypatch.setenv("KURA_BAYES_FORECAST_DAYS", "14")
    result = evaluate_strength_history(
        "bench_press",
        _strength_history([100, 101, 102, 104, 105, 106, 108, 109]),
        strength_engine="closed_form",
    )

    assert result["projection_type"] == "strength_inference"
    assert result["status"] == "ok"
    assert result["series_points"] == 8
    assert result["labeled_windows"] > 0
    assert result["metrics"]["mae"] is not None
    assert result["metrics"]["coverage_ci95"] is not None
    assert result["metrics"]["plateau_brier"] is not None
    assert result["metrics"]["velocity_mae"] is not None
    assert result["metrics"]["direction_accuracy"] is not None
    assert "closed_form" in result["engines_used"]


def test_evaluate_strength_history_insufficient_data():
    result = evaluate_strength_history(
        "bench_press",
        _strength_history([100, 101]),
        strength_engine="closed_form",
    )
    assert result["status"] == "insufficient_data"
    assert result["labeled_windows"] == 0
    assert result["metrics"]["mae"] is None


def test_evaluate_readiness_daily_scores_ok():
    result = evaluate_readiness_daily_scores(
        "overview",
        _readiness_daily([0.58, 0.6, 0.62, 0.61, 0.64, 0.66, 0.63, 0.67]),
    )
    assert result["projection_type"] == "readiness_inference"
    assert result["status"] == "ok"
    assert result["labeled_windows"] > 0
    assert result["metrics"]["mae_nowcast"] is not None
    assert result["metrics"]["coverage_ci95_nowcast"] is not None
    assert result["metrics"]["state_accuracy"] is not None
    assert result["metrics"]["velocity_mae_nowcast"] is not None
    assert result["metrics"]["direction_accuracy_nowcast"] is not None


def test_evaluate_readiness_daily_scores_insufficient_data():
    result = evaluate_readiness_daily_scores(
        "overview",
        _readiness_daily([0.6, 0.61, 0.62]),
    )
    assert result["status"] == "insufficient_data"
    assert result["metrics"]["mae_nowcast"] is None


def test_summarize_projection_results():
    summary = summarize_projection_results(
        [
            {
                "projection_type": "strength_inference",
                "status": "ok",
                "replay_windows": 5,
                "labeled_windows": 3,
            },
            {
                "projection_type": "strength_inference",
                "status": "insufficient_labels",
                "replay_windows": 2,
                "labeled_windows": 0,
            },
            {
                "projection_type": "readiness_inference",
                "status": "ok",
                "replay_windows": 6,
                "labeled_windows": 6,
            },
        ]
    )
    assert summary["strength_inference"]["projection_rows"] == 2
    assert summary["strength_inference"]["ok_rows"] == 1
    assert summary["strength_inference"]["replay_windows"] == 7
    assert summary["readiness_inference"]["labeled_windows"] == 6


def test_filter_retracted_event_rows_removes_target_event():
    rows = [
        {
            "id": "evt-1",
            "event_type": "set.logged",
            "timestamp": datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc),
            "data": {"exercise_id": "bench_press", "weight_kg": 100, "reps": 5},
            "metadata": {},
        },
        {
            "id": "evt-2",
            "event_type": "set.logged",
            "timestamp": datetime(2026, 2, 2, 10, 0, tzinfo=timezone.utc),
            "data": {"exercise_id": "bench_press", "weight_kg": 102, "reps": 5},
            "metadata": {},
        },
        {
            "id": "evt-r1",
            "event_type": "event.retracted",
            "timestamp": datetime(2026, 2, 3, 10, 0, tzinfo=timezone.utc),
            "data": {"retracted_event_id": "evt-1"},
            "metadata": {},
        },
    ]
    filtered = filter_retracted_event_rows(rows)
    ids = {row["id"] for row in filtered}
    assert ids == {"evt-2"}


def test_evaluate_from_event_store_rows_builds_strength_and_readiness(monkeypatch):
    monkeypatch.setenv("KURA_BAYES_FORECAST_DAYS", "7")

    rows = [
        {
            "id": "alias-1",
            "event_type": "exercise.alias_created",
            "timestamp": datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc),
            "data": {"alias": "Kniebeuge", "exercise_id": "barbell_back_squat"},
            "metadata": {},
        }
    ]
    for i in range(8):
        rows.append(
            {
                "id": f"set-{i}",
                "event_type": "set.logged",
                "timestamp": datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc) + timedelta(days=i * 7),
                "data": {"exercise": "Kniebeuge", "weight_kg": 100 + i, "reps": 5},
                "metadata": {"session_id": f"session-{i}"},
            }
        )
    for i in range(8):
        day = datetime(2026, 2, 1, 7, 0, tzinfo=timezone.utc) + timedelta(days=i)
        rows.append(
            {
                "id": f"sleep-{i}",
                "event_type": "sleep.logged",
                "timestamp": day,
                "data": {"duration_hours": 7.0 + (i * 0.1)},
                "metadata": {},
            }
        )
        rows.append(
            {
                "id": f"energy-{i}",
                "event_type": "energy.logged",
                "timestamp": day + timedelta(hours=1),
                "data": {"level": 6 + (i % 3)},
                "metadata": {},
            }
        )
        rows.append(
            {
                "id": f"soreness-{i}",
                "event_type": "soreness.logged",
                "timestamp": day + timedelta(hours=2),
                "data": {"severity": 2 + (i % 2)},
                "metadata": {},
            }
        )

    results = evaluate_from_event_store_rows(
        rows,
        projection_types=["strength_inference", "readiness_inference"],
        strength_engine="closed_form",
    )

    strength = [r for r in results if r["projection_type"] == "strength_inference"]
    readiness = [r for r in results if r["projection_type"] == "readiness_inference"]

    assert len(strength) == 1
    assert strength[0]["key"] == "barbell_back_squat"
    assert strength[0]["source"] == "event_store"
    assert strength[0]["status"] in {"ok", "insufficient_labels"}

    assert len(readiness) == 1
    assert readiness[0]["key"] == "overview"
    assert readiness[0]["source"] == "event_store"
    assert readiness[0]["status"] == "ok"


def test_summarize_projection_results_by_source():
    by_source = summarize_projection_results_by_source(
        [
            {
                "source": "projection_history",
                "projection_type": "strength_inference",
                "status": "ok",
                "replay_windows": 3,
                "labeled_windows": 2,
            },
            {
                "source": "event_store",
                "projection_type": "strength_inference",
                "status": "ok",
                "replay_windows": 4,
                "labeled_windows": 2,
            },
            {
                "source": "event_store",
                "projection_type": "readiness_inference",
                "status": "insufficient_labels",
                "replay_windows": 1,
                "labeled_windows": 0,
            },
        ]
    )
    assert by_source["projection_history"]["projection_rows"] == 1
    assert by_source["event_store"]["projection_rows"] == 2
    assert by_source["event_store"]["by_projection_type"]["strength_inference"]["ok_rows"] == 1


def test_build_semantic_labels_from_event_rows():
    rows = [
        {
            "id": "a1",
            "event_type": "exercise.alias_created",
            "timestamp": datetime(2026, 2, 1, 8, 0, tzinfo=timezone.utc),
            "data": {"alias": "Kniebeuge", "exercise_id": "barbell_back_squat"},
            "metadata": {},
        },
        {
            "id": "s1",
            "event_type": "set.logged",
            "timestamp": datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc),
            "data": {"exercise": "Bench Press", "exercise_id": "bench_press", "weight_kg": 100, "reps": 5},
            "metadata": {},
        },
    ]
    labels = build_semantic_labels_from_event_rows(rows)
    assert labels["kniebeuge"] == "barbell_back_squat"
    assert labels["bench press"] == "bench_press"


def test_evaluate_semantic_memory_projection_labels():
    labels = {"kniebeuge": "barbell_back_squat", "bankdruecken": "bench_press"}
    projection_data = {
        "exercise_candidates": [
            {
                "term": "kniebeuge",
                "suggested_exercise_id": "barbell_back_squat",
                "score": 0.91,
            },
            {
                "term": "bankdruecken",
                "suggested_exercise_id": "incline_bench_press",
                "score": 0.83,
            },
        ]
    }
    result = evaluate_semantic_memory_projection_labels("overview", projection_data, labels, top_k=5)
    assert result["projection_type"] == "semantic_memory"
    assert result["status"] == "ok"
    assert result["metrics"]["coverage"] == 1.0
    assert result["metrics"]["top1_accuracy"] == 0.5


def test_evaluate_semantic_event_store_labels_with_fake_provider(monkeypatch):
    class _FakeProvider:
        def embed_many(self, terms):
            mapping = {
                "kniebeuge": [1.0, 0.0],
                "bankdruecken": [0.0, 1.0],
            }
            return [mapping.get(term, [0.0, 0.0]) for term in terms]

    monkeypatch.setattr("kura_workers.eval_harness.get_embedding_provider", lambda: _FakeProvider())
    labels = {"kniebeuge": "barbell_back_squat", "bankdruecken": "bench_press"}
    catalog = [
        {"canonical_key": "barbell_back_squat", "embedding": [1.0, 0.0]},
        {"canonical_key": "bench_press", "embedding": [0.0, 1.0]},
    ]
    result = evaluate_semantic_event_store_labels(labels, catalog, top_k=3)
    assert result["status"] == "ok"
    assert result["metrics"]["top1_accuracy"] == 1.0
    assert result["metrics"]["topk_recall"] == 1.0
    assert result["metrics"]["mrr"] == 1.0


def test_shadow_mode_rollout_checks():
    results = [
        {
            "source": "event_store",
            "projection_type": "strength_inference",
            "status": "ok",
            "metrics": {"coverage_ci95": 0.85, "mae": 7.0},
        },
        {
            "source": "event_store",
            "projection_type": "readiness_inference",
            "status": "ok",
            "metrics": {"coverage_ci95_nowcast": 0.9, "mae_nowcast": 0.09},
        },
        {
            "source": "event_store",
            "projection_type": "semantic_memory",
            "status": "ok",
            "metrics": {"top1_accuracy": 0.8, "topk_recall": 0.95},
        },
    ]
    shadow = build_shadow_mode_rollout_checks(results, source_mode="event_store")
    assert shadow["status"] == "pass"
    assert shadow["allow_autonomous_behavior_changes"] is True
