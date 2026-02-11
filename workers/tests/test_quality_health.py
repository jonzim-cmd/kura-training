"""Tests for quality_health read-only invariant evaluation (Decision 13 Phase 0)."""

from datetime import datetime

from kura_workers.handlers.quality_health import (
    _autonomy_policy_from_slos,
    _auto_apply_decision,
    _build_detection_learning_signal_events,
    _build_quality_projection_data,
    _build_simulated_repair_proposals,
    _compute_integrity_slos,
    _compute_quality_score,
    _evaluate_read_only_invariants,
    _generate_repair_proposals,
    _simulate_repair_proposals,
)


def _row(event_type: str, data: dict) -> dict:
    return {"event_type": event_type, "data": data}


def _event_row(event_type: str, data: dict, iso_ts: str) -> dict:
    return {
        "event_type": event_type,
        "data": data,
        "timestamp": datetime.fromisoformat(iso_ts),
    }


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

    def test_detects_mention_bound_fields_missing_from_set_payload(self):
        rows = [
            _row(
                "set.logged",
                {
                    "exercise_id": "barbell_back_squat",
                    "reps": 5,
                    "notes": "Pause 90 sec, same for next sets",
                },
            ),
            _row("preference.set", {"key": "timezone", "value": "Europe/Berlin"}),
            _row("profile.updated", {"age_deferred": True, "bodyweight_deferred": True}),
        ]
        issues, metrics = _evaluate_read_only_invariants(rows, alias_map={})

        mention_issue = next(
            (issue for issue in issues if issue["type"] == "mention_field_missing"),
            None,
        )
        assert mention_issue is not None
        assert mention_issue["invariant_id"] == "INV-008"
        assert metrics["mention_field_missing_total"] >= 1
        assert mention_issue["metrics"]["sample_missing_fields"] == ["rest_seconds"]

    def test_no_mention_bound_issue_when_rest_is_persisted(self):
        rows = [
            _row(
                "set.logged",
                {
                    "exercise_id": "barbell_back_squat",
                    "reps": 5,
                    "notes": "Pause 90 sec",
                    "rest_seconds": 90,
                },
            ),
            _row("preference.set", {"key": "timezone", "value": "Europe/Berlin"}),
            _row("profile.updated", {"age_deferred": True, "bodyweight_deferred": True}),
        ]
        issues, _ = _evaluate_read_only_invariants(rows, alias_map={})
        assert all(issue["type"] != "mention_field_missing" for issue in issues)


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

    def test_detection_telemetry_events_cover_issue_and_simulation_states(self):
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
            },
            {
                "issue_id": "INV-003:timezone_missing",
                "invariant_id": "INV-003",
                "type": "timezone_missing",
                "severity": "high",
                "detail": "timezone missing",
                "metrics": {},
            },
        ]
        proposals = _build_simulated_repair_proposals(
            issues,
            evaluated_at="2026-02-11T10:00:00+00:00",
        )
        events = _build_detection_learning_signal_events(
            user_id="user-1",
            issues=issues,
            proposals=proposals,
            evaluated_at="2026-02-11T10:00:00+00:00",
            source_anchor="anchor-1",
        )

        signal_types = {
            event["data"]["signal_type"]
            for event in events
            if event["event_type"] == "learning.signal.logged"
        }
        assert "quality_issue_detected" in signal_types
        assert "repair_proposed" in signal_types
        assert "repair_simulated_safe" in signal_types
        assert "repair_simulated_risky" in signal_types


class TestAutoApplyPolicy:
    def test_tier_a_simulated_safe_is_auto_apply_eligible(self):
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
        proposals = _build_simulated_repair_proposals(
            issues,
            evaluated_at="2026-02-11T10:00:00+00:00",
        )
        assert len(proposals) == 1
        allowed, reason = _auto_apply_decision(proposals[0])
        assert allowed is True
        assert reason == "policy_pass"

    def test_risky_proposal_is_rejected_for_auto_apply(self):
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
        proposals = _build_simulated_repair_proposals(
            issues,
            evaluated_at="2026-02-11T10:00:00+00:00",
        )
        assert len(proposals) == 1
        allowed, reason = _auto_apply_decision(proposals[0])
        assert allowed is False
        assert reason in {"tier_not_a", "state_not_simulated_safe"}

    def test_non_deterministic_source_is_rejected(self):
        proposal = {
            "proposal_id": "repair:INV-001:unresolved_exercise_identity",
            "issue_id": "INV-001:unresolved_exercise_identity",
            "invariant_id": "INV-001",
            "issue_type": "unresolved_exercise_identity",
            "tier": "A",
            "state": "simulated_safe",
            "candidate_sources": ["slug_fallback"],
            "simulate": {"warnings": [], "projection_impacts": []},
            "proposed_event_batch": {
                "events": [
                    {
                        "event_type": "exercise.alias_created",
                        "data": {"alias": "x", "exercise_id": "x"},
                        "metadata": {"idempotency_key": "k"},
                    }
                ]
            },
        }
        allowed, reason = _auto_apply_decision(proposal)
        assert allowed is False
        assert reason == "non_deterministic_source"

    def test_auto_apply_can_be_disabled_by_autonomy_throttle(self):
        proposal = {
            "proposal_id": "repair:INV-001:unresolved_exercise_identity",
            "issue_id": "INV-001:unresolved_exercise_identity",
            "invariant_id": "INV-001",
            "issue_type": "unresolved_exercise_identity",
            "tier": "A",
            "state": "simulated_safe",
            "candidate_sources": ["catalog_variant_exact"],
            "simulate": {"warnings": [], "projection_impacts": []},
            "proposed_event_batch": {
                "events": [
                    {
                        "event_type": "exercise.alias_created",
                        "data": {"alias": "x", "exercise_id": "barbell_bench_press"},
                        "metadata": {"idempotency_key": "k"},
                    }
                ]
            },
        }
        allowed, reason = _auto_apply_decision(
            proposal,
            allow_tier_a_auto_apply=False,
        )
        assert allowed is False
        assert reason == "autonomy_throttled"


class TestIntegritySlos:
    def test_slo_status_degrades_on_mismatch_and_high_unresolved_rate(self):
        rows = [
            _event_row(
                "quality.save_claim.checked",
                {"mismatch_detected": True, "allow_saved_claim": False},
                "2026-02-11T09:00:00+00:00",
            ),
            _event_row(
                "quality.save_claim.checked",
                {"mismatch_detected": False, "allow_saved_claim": True},
                "2026-02-11T09:05:00+00:00",
            ),
        ]
        slos = _compute_integrity_slos(
            rows,
            metrics={"set_logged_unresolved_pct": 12.0, "set_logged_total": 10},
            evaluated_at="2026-02-11T10:00:00+00:00",
        )
        assert slos["status"] == "degraded"
        assert "unresolved_set_logged_pct" in slos["regressions"]
        assert (
            slos["metrics"]["save_claim_mismatch_rate_pct"]["value"] == 50.0
        )

    def test_autonomy_policy_throttles_on_degraded_slos(self):
        policy = _autonomy_policy_from_slos({"status": "degraded"})
        assert policy["throttle_active"] is True
        assert policy["max_scope_level"] == "strict"
        assert policy["repair_auto_apply_enabled"] is False
        assert "confirmation_templates" in policy
        assert "non_trivial_action" in policy["confirmation_templates"]

    def test_autonomy_policy_is_relaxed_when_slos_healthy(self):
        policy = _autonomy_policy_from_slos({"status": "healthy"})
        assert policy["throttle_active"] is False
        assert policy["max_scope_level"] == "moderate"
        assert policy["repair_auto_apply_enabled"] is True
        assert "post_save_followup" in policy["confirmation_templates"]
