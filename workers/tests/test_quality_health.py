"""Tests for quality_health read-only invariant evaluation (Decision 13 Phase 0)."""

from datetime import datetime, timezone

from kura_workers.handlers.quality_health import (
    _autonomy_policy_from_slos,
    _auto_apply_decision,
    _build_detection_learning_signal_events,
    _build_quality_projection_data,
    _build_simulated_repair_proposals,
    _compute_integrity_slos,
    _compute_quality_score,
    _compute_response_mode_outcomes,
    _evaluate_read_only_invariants,
    _generate_repair_proposals,
    _simulate_repair_proposals,
)


def _row(event_type: str, data: dict) -> dict:
    return {"event_type": event_type, "data": data}


def _event_row(
    event_type: str, data: dict, iso_ts: str, event_id: str | None = None
) -> dict:
    row = {
        "event_type": event_type,
        "data": data,
        "timestamp": datetime.fromisoformat(iso_ts),
    }
    if event_id is not None:
        row["id"] = event_id
    return row


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

    def test_detects_onboarding_phase_violation_without_close_or_override(self):
        rows = [
            _row("training_plan.created", {"name": "Starter plan"}),
            _row("preference.set", {"key": "timezone", "value": "Europe/Berlin"}),
            _row("profile.updated", {"age_deferred": True, "bodyweight_deferred": True}),
        ]
        issues, metrics = _evaluate_read_only_invariants(rows, alias_map={})

        issue = next((item for item in issues if item["invariant_id"] == "INV-004"), None)
        assert issue is not None
        assert issue["type"] == "onboarding_phase_violation"
        assert issue["metrics"]["planning_event_count"] == 1
        assert issue["metrics"]["onboarding_closed"] is False
        assert issue["metrics"]["override_present"] is False
        assert metrics["planning_event_total"] == 1
        assert metrics["onboarding_closed"] is False
        assert metrics["onboarding_override_present"] is False

    def test_onboarding_phase_violation_grandfathers_legacy_planning_events(self):
        rows = [
            _event_row(
                "training_plan.created",
                {"name": "Legacy plan"},
                "2026-02-11T13:00:00+00:00",
            ),
            _event_row(
                "preference.set",
                {"key": "timezone", "value": "Europe/Berlin"},
                "2026-02-11T13:05:00+00:00",
            ),
            _event_row(
                "profile.updated",
                {"age_deferred": True, "bodyweight_deferred": True},
                "2026-02-11T13:10:00+00:00",
            ),
        ]
        issues, metrics = _evaluate_read_only_invariants(rows, alias_map={})

        assert all(item["invariant_id"] != "INV-004" for item in issues)
        assert metrics["planning_event_total"] == 1
        assert metrics["planning_event_enforced_total"] == 0
        assert metrics["planning_event_legacy_total"] == 1
        assert metrics["inv004_legacy_policy_applied"] is True
        assert metrics["inv004_policy_cutoff"] == "2026-02-14T00:00:00+00:00"

    def test_onboarding_phase_violation_still_enforced_after_cutoff(self):
        rows = [
            _event_row(
                "training_plan.created",
                {"name": "Current plan"},
                "2026-02-15T09:00:00+00:00",
            ),
            _event_row(
                "preference.set",
                {"key": "timezone", "value": "Europe/Berlin"},
                "2026-02-15T09:05:00+00:00",
            ),
            _event_row(
                "profile.updated",
                {"age_deferred": True, "bodyweight_deferred": True},
                "2026-02-15T09:10:00+00:00",
            ),
        ]
        issues, metrics = _evaluate_read_only_invariants(rows, alias_map={})
        issue = next((item for item in issues if item["invariant_id"] == "INV-004"), None)
        assert issue is not None
        assert issue["metrics"]["planning_event_count"] == 1
        assert issue["metrics"]["legacy_grandfathered_count"] == 0
        assert metrics["planning_event_enforced_total"] == 1
        assert metrics["inv004_legacy_policy_applied"] is False

    def test_onboarding_phase_violation_clears_when_onboarding_closed(self):
        rows = [
            _row("workflow.onboarding.closed", {"reason": "summary confirmed"}),
            _row("training_plan.created", {"name": "Starter plan"}),
            _row("preference.set", {"key": "timezone", "value": "Europe/Berlin"}),
            _row("profile.updated", {"age_deferred": True, "bodyweight_deferred": True}),
        ]
        issues, metrics = _evaluate_read_only_invariants(rows, alias_map={})

        assert all(item["invariant_id"] != "INV-004" for item in issues)
        assert metrics["planning_event_total"] == 1
        assert metrics["onboarding_closed"] is True

    def test_onboarding_phase_violation_clears_when_override_present(self):
        rows = [
            _row(
                "workflow.onboarding.override_granted",
                {"reason": "user explicitly wants plan now"},
            ),
            _row("training_plan.updated", {"name": "Adjusted plan"}),
            _row("preference.set", {"key": "timezone", "value": "Europe/Berlin"}),
            _row("profile.updated", {"age_deferred": True, "bodyweight_deferred": True}),
        ]
        issues, metrics = _evaluate_read_only_invariants(rows, alias_map={})

        assert all(item["invariant_id"] != "INV-004" for item in issues)
        assert metrics["planning_event_total"] == 1
        assert metrics["onboarding_override_present"] is True

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

    def test_session_missing_anchor_rate_and_confidence_distribution_metrics(self):
        rows = [
            _row(
                "session.logged",
                {
                    "contract_version": "session.logged.v1",
                    "session_meta": {"sport": "running"},
                    "blocks": [
                        {
                            "block_type": "interval_endurance",
                            "dose": {
                                "work": {"duration_seconds": 120},
                                "recovery": {"duration_seconds": 60},
                                "repeats": 6,
                            },
                        }
                    ],
                    "provenance": {"source_type": "manual"},
                },
            ),
            _row(
                "session.logged",
                {
                    "contract_version": "session.logged.v1",
                    "session_meta": {"sport": "strength"},
                    "blocks": [
                        {
                            "block_type": "strength_set",
                            "dose": {"work": {"reps": 5}, "repeats": 1},
                            "intensity_anchors": [
                                {
                                    "measurement_state": "measured",
                                    "unit": "rpe",
                                    "value": 8,
                                }
                            ],
                        }
                    ],
                    "provenance": {"source_type": "manual"},
                },
            ),
            _row("preference.set", {"key": "timezone", "value": "Europe/Berlin"}),
            _row("profile.updated", {"age_deferred": True, "bodyweight_deferred": True}),
        ]
        issues, metrics = _evaluate_read_only_invariants(rows, alias_map={})

        issue_types = {issue["type"] for issue in issues}
        assert "session_missing_anchor_rate" in issue_types
        assert metrics["session_logged_total"] == 2
        assert metrics["session_missing_anchor_total"] == 1
        assert metrics["session_missing_anchor_rate_pct"] == 50.0
        distribution = metrics["session_confidence_distribution"]
        assert set(distribution.keys()) == {"low", "medium", "high"}
        assert sum(distribution.values()) == 2
        error_code_counts = metrics["session_error_code_counts"]
        assert "session.logged.anchor.missing" in error_code_counts

    def test_external_import_quality_signals_include_uncertainty_and_unsupported(self):
        rows = [
            _row(
                "external.activity_imported",
                {
                    "provenance": {
                        "unsupported_fields": ["workout.normalizedPower"],
                        "warnings": ["timezone inferred from UTC fallback"],
                        "field_provenance": {
                            "workout.calories_kcal": {
                                "confidence": 0.71,
                                "status": "estimated",
                                "unit_original": "kJ",
                                "unit_normalized": "kcal",
                            }
                        },
                    }
                },
            ),
            _row("preference.set", {"key": "timezone", "value": "Europe/Berlin"}),
            _row("profile.updated", {"age_deferred": True, "bodyweight_deferred": True}),
        ]
        import_jobs = [
            {
                "status": "completed",
                "error_code": None,
                "receipt": {"write": {"result": "duplicate_skipped"}},
            },
            {
                "status": "failed",
                "error_code": "version_conflict",
                "receipt": {},
            },
        ]
        issues, metrics = _evaluate_read_only_invariants(
            rows,
            alias_map={},
            import_job_rows=import_jobs,
        )

        issue_types = {issue["type"] for issue in issues}
        assert "external_unsupported_fields" in issue_types
        assert "external_low_confidence_fields" in issue_types
        assert "external_temporal_uncertainty" in issue_types
        assert "external_dedup_rejected" in issue_types
        assert metrics["external_imported_total"] == 1
        assert metrics["external_dedup_skipped_total"] == 1
        assert metrics["external_dedup_rejected_total"] == 1

    def test_external_import_parse_fail_rate_metric_is_reported(self):
        rows = [
            _row("preference.set", {"key": "timezone", "value": "Europe/Berlin"}),
            _row("profile.updated", {"age_deferred": True, "bodyweight_deferred": True}),
        ]
        import_jobs = [
            {"status": "failed", "error_code": "parse_error", "receipt": {}},
            {"status": "failed", "error_code": "validation_error", "receipt": {}},
            {"status": "completed", "error_code": None, "receipt": {}},
        ]
        issues, metrics = _evaluate_read_only_invariants(
            rows,
            alias_map={},
            import_job_rows=import_jobs,
        )

        assert metrics["external_import_job_total"] == 3
        assert metrics["external_import_parse_fail_total"] == 2
        assert metrics["external_import_parse_fail_rate_pct"] == 66.67
        assert metrics["external_import_error_class_counts"]["parse"] == 1
        assert metrics["external_import_error_class_counts"]["validation"] == 1
        issue_types = {issue["type"] for issue in issues}
        assert "external_parse_fail_rate" in issue_types

    def test_draft_hygiene_metrics_track_backlog_and_resolution(self):
        raw_rows = [
            _event_row(
                "observation.logged",
                {
                    "dimension": "provisional.persist_intent.training_session",
                    "context_text": "Draft A",
                },
                "2026-02-15T06:00:00+00:00",
                event_id="11111111-1111-1111-1111-111111111111",
            ),
            _event_row(
                "observation.logged",
                {
                    "dimension": "provisional.persist_intent.training_session",
                    "context_text": "Draft B",
                },
                "2026-02-15T07:00:00+00:00",
                event_id="22222222-2222-2222-2222-222222222222",
            ),
            _event_row(
                "event.retracted",
                {
                    "retracted_event_id": "11111111-1111-1111-1111-111111111111",
                    "retracted_event_type": "observation.logged",
                },
                "2026-02-15T08:00:00+00:00",
            ),
            _event_row(
                "preference.set",
                {"key": "timezone", "value": "Europe/Berlin"},
                "2026-02-15T08:05:00+00:00",
            ),
            _event_row(
                "profile.updated",
                {"age_deferred": True, "bodyweight_deferred": True},
                "2026-02-15T08:10:00+00:00",
            ),
        ]
        active_rows = [
            row
            for row in raw_rows
            if row.get("id") != "11111111-1111-1111-1111-111111111111"
        ]

        _, metrics = _evaluate_read_only_invariants(
            active_rows,
            alias_map={},
            raw_event_rows=raw_rows,
            evaluated_at=datetime.fromisoformat("2026-02-15T09:00:00+00:00"),
        )

        draft_hygiene = metrics["draft_hygiene"]
        assert draft_hygiene["backlog_open"] == 1
        assert draft_hygiene["opened_7d"] == 2
        assert draft_hygiene["closed_7d"] == 1
        assert draft_hygiene["resolution_rate_7d"] == 50.0
        assert draft_hygiene["status"] in {"healthy", "monitor", "degraded"}


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
        assert "draft_hygiene" in data
        assert data["issues_open"] == 1
        assert data["issues_by_severity"]["high"] == 1
        assert data["top_issues"][0]["invariant_id"] == "INV-003"
        assert data["invariant_mode"] == "read_only"
        assert "extraction_calibration" in data
        assert data["schema_capabilities"]["status"] == "healthy"

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
        provenance = proposals[0]["repair_provenance"]
        assert provenance["summary"]["by_source_type"]["inferred"] >= 1
        assert events[0]["data"]["repair_provenance"]["source_type"] in {
            "inferred",
            "estimated",
        }

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

    def test_detection_telemetry_dedupes_quality_issue_within_cooldown(self):
        issues = [
            {
                "issue_id": "INV-004:onboarding_phase_violation",
                "invariant_id": "INV-004",
                "type": "onboarding_phase_violation",
                "severity": "medium",
                "detail": "planning drift",
                "metrics": {},
            }
        ]
        proposals = _build_simulated_repair_proposals(
            issues,
            evaluated_at="2026-02-14T13:39:24+00:00",
        )
        history = {
            "INV-004:onboarding_phase_violation": {
                "timestamp": datetime(2026, 2, 14, 13, 0, 0, tzinfo=timezone.utc),
                "severity": "medium",
            }
        }
        events = _build_detection_learning_signal_events(
            user_id="user-1",
            issues=issues,
            proposals=proposals,
            evaluated_at="2026-02-14T13:39:24+00:00",
            source_anchor="anchor-1",
            quality_issue_history_by_issue=history,
        )
        signal_types = [event["data"]["signal_type"] for event in events]
        assert "quality_issue_detected" not in signal_types

    def test_detection_telemetry_re_emits_after_cooldown_or_severity_change(self):
        issues = [
            {
                "issue_id": "INV-004:onboarding_phase_violation",
                "invariant_id": "INV-004",
                "type": "onboarding_phase_violation",
                "severity": "high",
                "detail": "planning drift",
                "metrics": {},
            }
        ]
        proposals = _build_simulated_repair_proposals(
            issues,
            evaluated_at="2026-02-15T14:05:00+00:00",
        )
        history = {
            "INV-004:onboarding_phase_violation": {
                "timestamp": datetime(2026, 2, 14, 13, 0, 0, tzinfo=timezone.utc),
                "severity": "medium",
            }
        }
        events = _build_detection_learning_signal_events(
            user_id="user-1",
            issues=issues,
            proposals=proposals,
            evaluated_at="2026-02-15T14:05:00+00:00",
            source_anchor="anchor-1",
            quality_issue_history_by_issue=history,
        )
        detected_events = [
            event
            for event in events
            if event["data"]["signal_type"] == "quality_issue_detected"
        ]
        assert len(detected_events) == 1
        attrs = detected_events[0]["data"]["attributes"]
        assert attrs["issue_id"] == "INV-004:onboarding_phase_violation"
        assert attrs["severity"] == "high"


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

    def test_low_confidence_repair_is_rejected(self):
        proposal = {
            "proposal_id": "repair:INV-001:unresolved_exercise_identity",
            "issue_id": "INV-001:unresolved_exercise_identity",
            "invariant_id": "INV-001",
            "issue_type": "unresolved_exercise_identity",
            "tier": "A",
            "state": "simulated_safe",
            "candidate_sources": ["catalog_variant_exact"],
            "simulate": {"warnings": [], "projection_impacts": []},
            "repair_provenance": {
                "entries": [{
                    "source_type": "estimated",
                    "confidence": 0.3,
                    "confidence_band": "low",
                    "applies_scope": "session",
                    "reason": "guess",
                }],
                "summary": {
                    "entries": 1,
                    "by_source_type": {"estimated": 1},
                    "by_confidence_band": {"low": 1},
                    "low_confidence_entries": 1,
                },
            },
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
        allowed, reason = _auto_apply_decision(proposal)
        assert allowed is False
        assert reason == "low_confidence_repair"


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

    def test_protocol_friction_mismatch_does_not_degrade_integrity_slo(self):
        rows = [
            _event_row(
                "quality.save_claim.checked",
                {
                    "mismatch_detected": True,
                    "allow_saved_claim": False,
                    "verification_status": "pending",
                    "claim_status": "pending",
                    "uncertainty_markers": ["read_after_write_unverified"],
                },
                "2026-02-11T09:00:00+00:00",
            ),
            _event_row(
                "quality.save_claim.checked",
                {
                    "mismatch_detected": True,
                    "allow_saved_claim": False,
                    "verification_status": "pending",
                    "claim_status": "pending",
                },
                "2026-02-11T09:05:00+00:00",
            ),
        ]
        slos = _compute_integrity_slos(
            rows,
            metrics={"set_logged_unresolved_pct": 0.0, "set_logged_total": 10},
            evaluated_at="2026-02-11T10:00:00+00:00",
        )
        mismatch = slos["metrics"]["save_claim_mismatch_rate_pct"]
        assert mismatch["value"] == 0.0
        assert mismatch["protocol_friction_rate_pct"] > 0.0
        assert mismatch["status"] == "healthy"
        assert slos["status"] == "healthy"

    def test_posterior_risk_degrades_when_critical_mismatch_is_persistent(self):
        rows = []
        for idx in range(20):
            mismatch = idx < 8
            rows.append(
                _event_row(
                    "quality.save_claim.checked",
                    {
                        "mismatch_detected": mismatch,
                        "allow_saved_claim": not mismatch,
                        "mismatch_severity": "critical" if mismatch else "none",
                        "mismatch_weight": 1.0 if mismatch else 0.0,
                        "mismatch_domain": "save_echo" if mismatch else "none",
                    },
                    f"2026-02-11T09:{idx:02d}:00+00:00",
                )
            )

        slos = _compute_integrity_slos(
            rows,
            metrics={"set_logged_unresolved_pct": 0.0, "set_logged_total": 20},
            evaluated_at="2026-02-11T11:00:00+00:00",
        )
        mismatch = slos["metrics"]["save_claim_mismatch_rate_pct"]
        assert mismatch["sample_count"] == 20
        assert mismatch["mismatch_count"] == 8
        assert mismatch["value"] == 40.0
        assert mismatch["posterior_prob_gt_degraded"] >= 0.9
        assert mismatch["status"] == "degraded"
        assert slos["status"] == "degraded"

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

    def test_autonomy_policy_disables_repair_auto_apply_when_calibration_degraded(self):
        policy = _autonomy_policy_from_slos(
            {"status": "healthy"},
            calibration_status="degraded",
        )
        assert policy["calibration_status"] == "degraded"
        assert policy["throttle_active"] is True
        assert policy["repair_auto_apply_enabled"] is False
        assert policy["require_confirmation_for_repairs"] is True

    def test_autonomy_policy_throttles_repair_when_calibration_monitor(self):
        policy = _autonomy_policy_from_slos(
            {"status": "healthy"},
            calibration_status="monitor",
        )
        assert policy["calibration_status"] == "monitor"
        assert policy["throttle_active"] is True
        assert policy["require_confirmation_for_repairs"] is True
        assert policy["repair_auto_apply_enabled"] is False


class TestResponseModeOutcomes:
    def test_outcome_metrics_enforce_sample_floor_before_tuning(self):
        rows = []
        for _ in range(4):
            rows.append(
                {
                    "data": {
                        "signal_type": "response_mode_selected",
                        "attributes": {"mode_code": "B"},
                    }
                }
            )
            rows.append(
                {
                    "data": {
                        "signal_type": "post_task_reflection_confirmed",
                        "attributes": {},
                    }
                }
            )

        outcomes = _compute_response_mode_outcomes(rows)
        assert outcomes["response_mode_selected_total"] == 4
        assert outcomes["post_task_reflection_total"] == 4
        assert outcomes["sample_ok"] is False
        assert outcomes["sample_confidence"] == "low"

    def test_outcome_metrics_track_follow_through_challenge_and_regret(self):
        rows = []
        for idx in range(10):
            mode = "A"
            if idx in {6, 7, 8}:
                mode = "B"
            if idx == 9:
                mode = "C"
            rows.append(
                {
                    "data": {
                        "signal_type": "response_mode_selected",
                        "attributes": {"mode_code": mode},
                    }
                }
            )
            rows.append(
                {
                    "data": {
                        "signal_type": (
                            "post_task_reflection_confirmed" if idx < 7 else "post_task_reflection_unresolved"
                        ),
                        "attributes": {},
                    }
                }
            )

        for idx in range(8):
            rows.append(
                {
                    "data": {
                        "signal_type": "retrieval_regret_observed",
                        "attributes": {"threshold_exceeded": idx < 3},
                    }
                }
            )

        rows.extend(
            [
                {"data": {"signal_type": "workflow_override_used", "attributes": {}}},
                {"data": {"signal_type": "workflow_override_used", "attributes": {}}},
                {"data": {"signal_type": "correction_applied", "attributes": {}}},
            ]
        )
        rows.extend(
            [{"data": {"signal_type": "save_handshake_verified", "attributes": {}}} for _ in range(7)]
        )
        rows.extend(
            [{"data": {"signal_type": "save_handshake_pending", "attributes": {}}} for _ in range(2)]
        )
        rows.append({"data": {"signal_type": "save_claim_mismatch_attempt", "attributes": {}}})

        outcomes = _compute_response_mode_outcomes(rows)
        assert outcomes["sample_ok"] is True
        assert outcomes["sample_confidence"] == "medium"
        assert outcomes["response_mode_selected_total"] == 10
        assert outcomes["response_mode_general_share_pct"] == 10.0
        assert outcomes["post_task_follow_through_rate_pct"] == 70.0
        assert outcomes["retrieval_regret_exceeded_pct"] == 37.5
        assert outcomes["user_challenge_rate_pct"] == 20.0
        assert outcomes["save_handshake_verified_rate_pct"] == 70.0
