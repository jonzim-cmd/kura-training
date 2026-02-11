"""Tests for quality_health read-only invariant evaluation (Decision 13 Phase 0)."""

from kura_workers.handlers.quality_health import (
    _build_quality_projection_data,
    _compute_quality_score,
    _evaluate_read_only_invariants,
    _generate_repair_proposals,
    _simulate_repair_proposals,
)


def _row(event_type: str, data: dict) -> dict:
    return {"event_type": event_type, "data": data}


class TestEvaluateReadOnlyInvariants:
    def test_detects_unresolved_set_identity(self):
        rows = [
            _row("set.logged", {"exercise": "mystery cable move", "reps": 10, "weight_kg": 25}),
            _row("preference.set", {"key": "unit_system", "value": "metric"}),
        ]
        issues, metrics = _evaluate_read_only_invariants(rows, alias_map={})

        inv_ids = {issue["invariant_id"] for issue in issues}
        assert "INV-001" in inv_ids
        assert metrics["set_logged_total"] == 1
        assert metrics["set_logged_unresolved"] == 1

    def test_alias_resolves_without_issue(self):
        rows = [
            _row("set.logged", {"exercise": "clean", "reps": 3, "weight_kg": 60}),
            _row("preference.set", {"key": "timezone", "value": "Europe/Berlin"}),
            _row("profile.updated", {"age_deferred": True, "bodyweight_deferred": True}),
        ]
        issues, metrics = _evaluate_read_only_invariants(
            rows, alias_map={"clean": "barbell_clean"}
        )

        assert all(issue["invariant_id"] != "INV-001" for issue in issues)
        assert metrics["set_logged_unresolved"] == 0

    def test_detects_timezone_missing(self):
        rows = [
            _row("set.logged", {"exercise_id": "barbell_back_squat", "reps": 5, "weight_kg": 100}),
            _row("profile.updated", {"age_deferred": True, "bodyweight_deferred": True}),
        ]
        issues, _ = _evaluate_read_only_invariants(rows, alias_map={})

        issue_types = {issue["type"] for issue in issues}
        assert "timezone_missing" in issue_types

    def test_goal_trackability_issue_for_jump_goal_without_path(self):
        rows = [
            _row("goal.set", {"description": "Ich will dunken koennen"}),
            _row("preference.set", {"key": "timezone", "value": "Europe/Berlin"}),
            _row("profile.updated", {"age_deferred": True, "bodyweight_deferred": True}),
        ]
        issues, _ = _evaluate_read_only_invariants(rows, alias_map={})

        issue_types = {issue["type"] for issue in issues}
        assert "goal_trackability_missing" in issue_types

    def test_deferred_baseline_avoids_inv_006(self):
        rows = [
            _row("preference.set", {"key": "timezone", "value": "Europe/Berlin"}),
            _row(
                "profile.updated",
                {"age_deferred": True, "bodyweight_deferred": True},
            ),
        ]
        issues, _ = _evaluate_read_only_invariants(rows, alias_map={})

        assert all(issue["invariant_id"] != "INV-006" for issue in issues)


class TestQualityProjectionData:
    def test_quality_score_penalizes_by_severity(self):
        issues = [
            {"invariant_id": "INV-001", "type": "a", "severity": "high", "issue_id": "1", "detail": "x", "metrics": {}},
            {"invariant_id": "INV-006", "type": "b", "severity": "medium", "issue_id": "2", "detail": "y", "metrics": {}},
        ]
        score = _compute_quality_score(issues)
        assert score < 1.0
        assert score > 0.0

    def test_projection_shape(self):
        issues = [
            {
                "issue_id": "INV-003:timezone_missing",
                "invariant_id": "INV-003",
                "type": "timezone_missing",
                "severity": "high",
                "detail": "No timezone",
                "metrics": {},
            }
        ]
        data = _build_quality_projection_data(
            issues,
            metrics={"set_logged_total": 0},
            evaluated_at="2026-02-11T10:00:00+00:00",
        )

        assert data["status"] == "degraded"
        assert data["issues_open"] == 1
        assert data["issues_by_severity"]["high"] == 1
        assert data["top_issues"][0]["invariant_id"] == "INV-003"
        assert data["invariant_mode"] == "read_only"

    def test_projection_includes_repair_proposals(self):
        issues = [
            {
                "issue_id": "INV-001:unresolved_exercise_identity",
                "invariant_id": "INV-001",
                "type": "unresolved_exercise_identity",
                "severity": "high",
                "detail": "identity gap",
                "metrics": {
                    "top_unresolved_terms_with_counts": [
                        {"term": "bench press", "count": 2},
                    ],
                },
            }
        ]
        data = _build_quality_projection_data(
            issues,
            metrics={"set_logged_total": 2},
            evaluated_at="2026-02-11T10:00:00+00:00",
        )

        assert data["repair_proposals_total"] == 1
        proposal = data["repair_proposals"][0]
        assert proposal["issue_id"] == "INV-001:unresolved_exercise_identity"
        assert proposal["state"] == "simulated_safe"
        assert proposal["safe_for_apply"] is True
        assert data["repair_apply_ready_ids"] == [proposal["proposal_id"]]


class TestRepairProposals:
    def test_inv001_generates_alias_repair_events(self):
        issues = [
            {
                "issue_id": "INV-001:unresolved_exercise_identity",
                "invariant_id": "INV-001",
                "type": "unresolved_exercise_identity",
                "severity": "high",
                "detail": "identity gap",
                "metrics": {
                    "top_unresolved_terms_with_counts": [
                        {"term": "bench press", "count": 3},
                    ],
                },
            }
        ]
        proposals = _generate_repair_proposals(
            issues,
            evaluated_at="2026-02-11T10:00:00+00:00",
        )
        assert len(proposals) == 1
        events = proposals[0]["proposed_event_batch"]["events"]
        assert len(events) == 1
        assert events[0]["event_type"] == "exercise.alias_created"
        assert events[0]["data"]["exercise_id"] == "barbell_bench_press"

    def test_inv003_is_simulated_risky(self):
        issues = [
            {
                "issue_id": "INV-003:timezone_missing",
                "invariant_id": "INV-003",
                "type": "timezone_missing",
                "severity": "high",
                "detail": "timezone missing",
                "metrics": {},
            }
        ]
        proposals = _simulate_repair_proposals(
            _generate_repair_proposals(
                issues,
                evaluated_at="2026-02-11T10:00:00+00:00",
            ),
            evaluated_at="2026-02-11T10:00:00+00:00",
        )
        assert len(proposals) == 1
        proposal = proposals[0]
        assert proposal["state"] == "simulated_risky"
        assert proposal["safe_for_apply"] is False
        assert any("UTC assumption" in note for note in proposal["simulate"]["notes"])
