"""System config — deployment-static configuration for the agent.

Builds the complete system layer from handler declarations, event conventions,
interview guide, and normalization conventions. Written to the system_config
table on worker startup. Changes only when code is deployed.

The agent reads this once per session (or the MCP server caches it at startup)
to understand: what dimensions exist, what events are available, how to log
data correctly, and how to conduct onboarding interviews.
"""

import logging
from typing import Any

import psycopg
from psycopg.types.json import Json

from .event_conventions import get_event_conventions
from .external_import_error_taxonomy import external_import_error_taxonomy_v1
from .external_import_mapping_v2 import import_mapping_contract_v2
from .interview_guide import get_interview_guide
from .registry import get_dimension_metadata
from .training_hardening_gate_v1 import hardening_gate_contract_v1
from .training_legacy_compat import legacy_compat_contract_v1
from .training_load_calibration_v1 import calibration_protocol_v1
from .training_load_v2 import load_projection_contract_v2
from .training_rollout_v1 import rollout_contract_v1
from .training_session_completeness import completeness_policy_v1
from .training_session_contract import block_catalog_v1
from .training_core_fields import core_field_registry

logger = logging.getLogger(__name__)


def _get_conventions() -> dict[str, Any]:
    """Return normalization conventions for the agent.

    These tell the agent HOW to log data correctly, preventing
    fragmentation issues like exercises without exercise_id.
    """
    return {
        "exercise_normalization": {
            "rules": [
                "ALWAYS set exercise_id when you recognize the exercise.",
                "When setting both exercise + exercise_id for a user term the first time, "
                "also create exercise.alias_created in the same batch.",
                "When uncertain about the canonical name, ask the user.",
                "Only omit exercise_id when the exercise is truly unknown to you.",
                "Check user.aliases for existing mappings before creating new ones.",
            ],
            "example_batch": [
                {
                    "event_type": "set.logged",
                    "data": {
                        "exercise": "Kniebeuge",
                        "exercise_id": "barbell_back_squat",
                        "weight_kg": 100,
                        "reps": 5,
                    },
                },
                {
                    "event_type": "exercise.alias_created",
                    "data": {
                        "alias": "Kniebeuge",
                        "exercise_id": "barbell_back_squat",
                        "confidence": "confirmed",
                    },
                },
            ],
        },
        "training_core_fields_v1": {
            "rules": [
                "Mention-to-field extraction must be deterministic (regex/rules, not hidden heuristics).",
                "Optional fields remain unset unless explicitly provided or deterministically mentioned.",
                "Mention-bound fields (e.g. rest_seconds, tempo, RIR context) become mandatory to persist once mentioned.",
                "Session defaults apply within the same session + exercise scope until overridden.",
            ],
            "modality_registry": core_field_registry(),
            "note": (
                "Quality checks flag mention-present/field-missing mismatches with remediation hints."
            ),
        },
        "training_session_block_model_v1": {
            "rules": [
                "Use session.logged for modality-neutral block-based sessions.",
                "Apply block-specific completeness rules instead of global sensor requirements.",
                "No global HR requirement: missing sensor values must be explicit via measurement_state.",
                "Performance blocks require intensity anchors unless explicitly marked not_applicable.",
                "Clarifications must be block-relevant and minimal: ask only for missing log_valid fields of the affected block.",
            ],
            "event_type": "session.logged",
            "contract": block_catalog_v1(),
            "completeness_policy": completeness_policy_v1(),
        },
        "training_load_projection_v2": {
            "rules": [
                "Projection v2 aggregates modality-specific load and global load in one contract.",
                "Manual-only logging remains valid for analytics; confidence degrades when sparse.",
                "Sensor enrichment improves confidence and analysis tier without schema migrations.",
                "Feature-flag rollback must keep legacy timeline summaries available.",
            ],
            "projection_type": "training_timeline",
            "contract": load_projection_contract_v2(),
        },
        "training_load_calibration_v1": {
            "rules": [
                "Calibration uses deterministic replay inputs and versioned parameter profiles.",
                "Promotion of calibrated parameters requires shadow guardrails to pass.",
                "Rollback to baseline parameters must be possible via feature flag without schema changes.",
            ],
            "contract": calibration_protocol_v1(),
        },
        "session_legacy_compatibility_v1": {
            "rules": [
                "Legacy set.logged and session.logged v1 must coexist without double counting.",
                "Backfill from set.logged to session.logged must be append-only and idempotent.",
                "Compatibility adapter must remain deterministic for replay safety.",
            ],
            "contract": legacy_compat_contract_v1(),
        },
        "training_rollout_guard_v1": {
            "rules": [
                "Rollout uses QA matrix cohorts (strength, sprint, endurance, hybrid, low-data).",
                "Shadow-mode comparison is required before promotion.",
                "Feature flags must provide rollback path without data loss.",
                "Monitoring must expose parse-fail-rate, missing-anchor-rate, and confidence distribution.",
            ],
            "contract": rollout_contract_v1(),
        },
        "training_hardening_gate_v1": {
            "rules": [
                "Hardening gate must be green before production ramp-up.",
                "Gate combines error taxonomy, calibration, import mapping, and rollback readiness.",
                "Gate decision is binary and machine-readable (pass|fail).",
            ],
            "contract": hardening_gate_contract_v1(),
        },
        "external_import_mapping_v2": {
            "rules": [
                "Provider/format matrix declares supported|partial|not_available by canonical field.",
                "Imports map into the same session.logged block taxonomy as manual logging.",
                "No provider-specific field may become a global hard requirement.",
            ],
            "contract": import_mapping_contract_v2(),
        },
        "external_import_error_taxonomy_v1": {
            "rules": [
                "Import error monitoring uses stable error-code classes instead of message parsing.",
                "Parse quality failures include parse, mapping, and validation classes.",
                "Dedup conflicts are tracked separately from parse quality failure rates.",
            ],
            "contract": external_import_error_taxonomy_v1(),
        },
        "load_context_v1": {
            "rules": [
                "Persist load_context when equipment or movement context affects comparability.",
                "Use load_context.comparability_group to prevent semantic mixing (e.g. smith vs free_weight).",
                "If context changes after correction, recompute progression for old and new canonical targets.",
                "When comparability is unclear, keep group as unresolved/unspecified and ask a single clarification.",
            ],
            "event_type": "set.logged",
            "required_fields_when_present": [
                "load_context.implements_type",
                "load_context.equipment_profile",
                "load_context.comparability_group",
            ],
            "examples": [
                {
                    "exercise_id": "bulgarian_split_squat_smith",
                    "load_context": {
                        "implements_type": "machine",
                        "equipment_profile": "smith_machine",
                        "comparability_group": "machine:smith",
                    },
                },
                {
                    "exercise_id": "barbell_back_squat",
                    "load_context": {
                        "implements_type": "barbell",
                        "equipment_profile": "barbell",
                        "comparability_group": "free_weight",
                    },
                },
            ],
        },
        "session_feedback_certainty_v1": {
            "rules": [
                "Subjective fields use explicit certainty states: confirmed|inferred|unresolved.",
                "Inferred values require matching evidence claim ids for traceability.",
                "Unresolved values are persisted as unresolved state + reason, without numeric guessing.",
                "If no certainty markers are provided, explicit numeric values are treated as confirmed (legacy compatibility).",
            ],
            "event_type": "session.completed",
            "contract_fields": [
                "<field>",
                "<field>_state",
                "<field>_source",
                "<field>_evidence_claim_id",
                "<field>_unresolved_reason",
            ],
            "covered_fields": [
                "enjoyment",
                "perceived_quality",
                "perceived_exertion",
            ],
        },
        "schema_capability_gate_v1": {
            "rules": [
                "Optional relation reads must be capability-gated (to_regclass check) before querying.",
                "Projection recompute continues with degraded mode when optional relations are missing.",
                "Degraded capability state is exposed in projection payloads for agent-visible diagnostics.",
            ],
            "required_relation_checks": [
                {
                    "relation": "external_import_jobs",
                    "required_by": ["quality_health", "training_timeline"],
                    "fallback_behavior": "skip_import_job_enrichment",
                }
            ],
        },
        "evidence_layer_v1": {
            "rules": [
                "Deterministic parsers must emit claim lineage for mention-derived fields.",
                "Each claim must include confidence plus source_text_span provenance.",
                "Evidence claims link to the persisted target event via lineage.event_id.",
            ],
            "parser_version": "mention_parser.v1",
            "event_type": "evidence.claim.logged",
            "required_fields": [
                "claim_id",
                "claim_type",
                "value",
                "scope",
                "confidence",
                "provenance.source_text_span",
                "provenance.parser_version",
                "lineage.event_id",
            ],
        },
        "open_observation_v1": {
            "rules": [
                "Use observation.logged when a useful fact does not fit fixed event schemas.",
                "Always preserve raw context_text and provenance when available.",
                "Known dimensions get typed normalization; provisional/unknown stay open-world with quality flags.",
                "Keep confidence explicit (0..1); if unknown, set a conservative default.",
            ],
            "event_type": "observation.logged",
            "projection_type": "open_observations",
            "registry_version": "open_observation.v1",
            "validation_tiers": {
                "known": ["motivation_pre", "discomfort_signal", "jump_baseline"],
                "provisional_prefixes": ["x_", "custom.", "provisional."],
                "unknown_behavior": "store_with_quality_flags",
            },
            "lifecycle_policy": {
                "states": ["known", "provisional", "unknown"],
                "promotion_status_values": [
                    "already_known",
                    "insufficient_support",
                    "confidence_below_threshold",
                    "eligible_for_human_review",
                ],
                "thresholds": {
                    "promotion_min_support": 5,
                    "promotion_min_avg_confidence": 0.8,
                },
                "non_goals": [
                    "No automatic schema mutation from projection-only signals.",
                    "No autonomous promotion to known without explicit human review.",
                ],
            },
            "required_fields": [
                "dimension",
                "value",
                "confidence",
                "context_text",
                "provenance",
            ],
        },
        "ingestion_locale_v1": {
            "rules": [
                "Carry locale hints when known (language, region, timezone) to reduce parsing ambiguity.",
                "Normalize decimal comma and decimal dot consistently for numeric fields.",
                "Prefer canonical units in persisted events; preserve original unit context in provenance when available.",
                "If parsing confidence is low, mark uncertainty explicitly and ask for clarification.",
            ],
            "numeric_normalization": {
                "decimal_comma_example": "8,5 -> 8.5",
                "mixed_separator_policy": (
                    "If both comma and dot exist, infer decimal separator from the rightmost symbol."
                ),
            },
            "date_time_normalization": {
                "timezone_required_for_temporal_claims": True,
                "fallback_policy": "explicit_assumption_or_clarification",
            },
            "terminology_policy": {
                "source": "language-aware synonym registry + canonical IDs",
                "unknown_term_behavior": "store_as_observation_or_request_clarification",
            },
        },
        "learning_clustering_v1": {
            "rules": [
                "Cluster learning.signal.logged by stable cluster_signature only (deterministic, no hidden ML merge).",
                "Score clusters with explainable factors: frequency * severity * impact * reproducibility.",
                "Persist representative examples and workflow phases for auditability.",
                "Apply false-positive controls before persistence (minimum support + cross-user recurrence).",
            ],
            "source_event_type": "learning.signal.logged",
            "refresh_job": "inference.nightly_refit",
            "output_table": "learning_issue_clusters",
            "run_table": "learning_issue_cluster_runs",
            "period_granularities": ["day", "week"],
            "score_formula": "priority = frequency * severity * impact * reproducibility",
            "false_positive_controls": {
                "min_support_default": 3,
                "min_unique_users_default": 2,
                "include_low_confidence_default": False,
                "confidence_band_policy": "exclude low confidence by default",
            },
            "score_factors": {
                "frequency": "min(1.0, event_count / frequency_reference_count)",
                "severity": "average per-signal severity weight (confidence-adjusted)",
                "impact": "average per-signal outcome-impact weight",
                "reproducibility": (
                    "mean(user_coverage, repeatability) with user_coverage="
                    "min(1.0, unique_users / reproducibility_reference_users)"
                ),
            },
        },
        "extraction_calibration_v1": {
            "rules": [
                "Evaluate evidence.claim.logged confidence against correction/retraction outcomes.",
                "Compute deterministic calibration metrics per claim_class and parser_version.",
                "Emit weekly underperforming classes when brier/precision or drift thresholds are breached.",
                "Feed calibration status into autonomy policy to throttle auto-repair aggressiveness.",
            ],
            "source_event_type": "evidence.claim.logged",
            "refresh_job": "inference.nightly_refit",
            "output_table": "extraction_calibration_metrics",
            "underperforming_table": "extraction_underperforming_classes",
            "run_table": "extraction_calibration_runs",
            "period_granularities": ["day", "week"],
            "metrics": [
                "brier_score",
                "precision_high_conf",
                "recall_high_conf",
                "sample_count",
            ],
            "defaults": {
                "high_conf_threshold": 0.86,
                "min_samples_for_status": 3,
                "brier_monitor_max": 0.20,
                "brier_degraded_max": 0.30,
                "precision_monitor_min": 0.70,
                "precision_degraded_min": 0.55,
                "drift_delta_brier_alert": 0.06,
            },
            "policy_integration": {
                "autonomy_policy_field": "calibration_status",
                "degraded_effect": "disable_auto_repair",
                "monitor_effect": "throttle_auto_repair",
            },
        },
        "model_tier_registry_v1": {
            "rules": [
                "All models start at moderate tier regardless of identity.",
                "Auto-tiering adjusts tier based on observed quality (mismatch rate over 30 days).",
                "Model identity is used for audit/logging and quality track separation only.",
                "Strict tier additionally requires intent_handshake for high-impact writes.",
                "Tiers control autonomy (confirmation before write), NOT integrity reporting (echo after write).",
                "Save-Echo is tier-independent and always required — see save_echo_policy_v1.",
            ],
            "identity_resolution": {
                "trusted_sources_order": [
                    "model_attestation (HMAC-verified runtime identity)",
                    "oauth_access_token.client_id -> KURA_AGENT_MODEL_BY_CLIENT_ID_JSON",
                    "KURA_AGENT_MODEL_IDENTITY",
                    "unknown",
                ],
                "identity_purpose": "audit_and_quality_track_separation",
                "identity_does_not_affect_tier": True,
            },
            "default_start_tier": "moderate",
            "tiers": {
                "strict": {
                    "confidence_floor": 0.90,
                    "allowed_action_scope": "strict",
                    "high_impact_write_policy": "confirm_first",
                    "intent_handshake_required": True,
                    "repair_auto_apply_cap": "confirm_only",
                },
                "moderate": {
                    "confidence_floor": 0.80,
                    "allowed_action_scope": "moderate",
                    "high_impact_write_policy": "confirm_first",
                    "intent_handshake_required": False,
                    "repair_auto_apply_cap": "confirm_only",
                },
                "advanced": {
                    "confidence_floor": 0.70,
                    "allowed_action_scope": "proactive",
                    "high_impact_write_policy": "allow",
                    "intent_handshake_required": False,
                    "repair_auto_apply_cap": "enabled",
                },
            },
            "auto_tiering": {
                "lookback_days": 30,
                "min_samples": 12,
                "advanced_max_mismatch_pct": 0.60,
                "moderate_max_mismatch_pct": 4.00,
                "hysteresis_enabled": True,
            },
            "policy_outputs": [
                "capability_tier",
                "allowed_action_scope",
                "high_impact_write_policy",
                "repair_auto_apply_cap",
                "reason_codes",
            ],
        },
        "response_mode_policy_v1": {
            "rules": [
                "Select response mode by evidence state, not by forced personalization.",
                "Mode A (grounded_personalized) requires verified write-proof evidence.",
                "Mode B (hypothesis_personalized) allows tentative personalization with explicit uncertainty.",
                "Mode C (general_guidance) keeps recommendations generic and asks one high-value clarification.",
                "Policy role is advisory-only nudge logic; it must never hard-block autonomy gates.",
            ],
            "schema_version": "response_mode_policy.v1",
            "policy_role": "nudge_only",
            "modes": {
                "A": {
                    "name": "grounded_personalized",
                    "evidence_state": "sufficient",
                    "require_transparency_note": False,
                },
                "B": {
                    "name": "hypothesis_personalized",
                    "evidence_state": "limited",
                    "require_transparency_note": True,
                },
                "C": {
                    "name": "general_guidance",
                    "evidence_state": "insufficient",
                    "require_transparency_note": True,
                },
            },
            "switch_logic": {
                "A_if": (
                    "verification.status!=failed AND claim_guard.allow_saved_claim=true "
                    "AND evidence_score >= threshold_a_min"
                ),
                "B_if": "verification.status!=failed AND evidence_score >= threshold_b_min",
                "C_else": "fallback when personalized evidence is not reliable enough",
            },
            "evidence_score": {
                "range": [0.0, 1.0],
                "components": [
                    "read_after_write_coverage",
                    "claim_guard_saved_claim",
                    "integrity_slo_context",
                    "calibration_context",
                    "historical_unresolved_rate",
                    "save_claim_posterior_risk",
                    "challenge_rate",
                    "follow_through_rate",
                    "retrieval_regret_exceeded_rate",
                    "save_handshake_verified_rate",
                ],
                "penalty_policy": "non-linear penalties for degraded integrity/calibration context",
            },
            "outcome_tuning": {
                "window_days": 14,
                "sample_floor": {
                    "response_mode_selected_total": 8,
                    "post_task_reflection_total": 8,
                },
                "apply_only_when_sample_ok": True,
                "principle": (
                    "Tighten thresholds when regret/challenge rises, relax conservatively "
                    "when follow-through and save-handshake verification stay strong."
                ),
            },
            "adaptive_thresholds": {
                "base": {"A_min": 0.72, "B_min": 0.42},
                "adjustments": {
                    "integrity.monitor": {"A_plus": 0.05, "B_plus": 0.03},
                    "integrity.degraded": {"A_plus": 0.12, "B_plus": 0.08},
                    "calibration.monitor": {"A_plus": 0.04, "B_plus": 0.00},
                    "calibration.degraded": {"A_plus": 0.10, "B_plus": 0.05},
                    "quality.monitor": {"A_plus": 0.02, "B_plus": 0.00},
                    "quality.degraded": {"A_plus": 0.05, "B_plus": 0.03},
                    "outcomes.high_regret_or_challenge_or_low_follow_through": {
                        "A_plus": 0.04,
                        "B_plus": 0.03,
                    },
                    "outcomes.consistently_stable": {"A_minus": 0.02, "B_minus": 0.01},
                },
            },
            "safety": {
                "no_forced_personalization": True,
                "no_autonomy_blocking_from_mode_policy": True,
            },
        },
        "personal_failure_profile_v1": {
            "rules": [
                "Maintain a deterministic per-user x model profile key for recurring failure patterns.",
                "Track active failure modes as weighted signals, not binary labels.",
                "Use profile only as advisory context for communication strategy and evidence disclosure.",
                "Profile must never cage model capability; it can nudge response mode only.",
            ],
            "schema_version": "personal_failure_profile.v1",
            "keying": {
                "profile_id_seed": "user_id + model_identity + schema_version",
                "profile_id_format": "pfp_<stable_hash>",
            },
            "policy_role": "advisory_only",
            "required_fields": [
                "profile_id",
                "model_identity",
                "data_quality_band",
                "recommended_response_mode",
                "active_signals[]",
            ],
        },
        "sidecar_retrieval_regret_v1": {
            "rules": [
                "LaaJ runs as sidecar (advisory), never as authority over autonomy gate.",
                "Retrieval-regret is measured continuously and exposed with score + band + reason codes.",
                "High regret should trigger transparency and one clarification question, not bureaucratic friction.",
                "Sidecar output must remain explainable and lightweight for chat UX.",
            ],
            "laaj_sidecar": {
                "schema_version": "laaj_sidecar.v1",
                "policy_role": "advisory_only",
                "must_not_block_autonomy": True,
                "verdicts": ["pass", "review"],
            },
            "retrieval_regret": {
                "schema_version": "retrieval_regret.v1",
                "score_range": [0.0, 1.0],
                "bands": ["low", "medium", "high"],
                "threshold_default": 0.45,
                "adaptive_thresholds": {
                    "integrity_or_calibration_degraded": 0.35,
                    "integrity_or_calibration_monitor": 0.40,
                    "healthy_default": 0.45,
                },
            },
            "event_contract": {
                "event_type": "learning.signal.logged",
                "signal_types": [
                    "response_mode_selected",
                    "personal_failure_profile_observed",
                    "retrieval_regret_observed",
                    "laaj_sidecar_assessed",
                ],
            },
        },
        "learning_backlog_bridge_v1": {
            "rules": [
                "Generate machine-readable issue candidates from weekly learning_issue_clusters and extraction underperformance reports.",
                "When unknown_dimension proposals are accepted, route them into backlog candidates with source_type=unknown_dimension.",
                "Keep candidate creation approval-gated for humans in V1; do not auto-create tracker issues.",
                "Attach root-cause hypothesis, impacted metrics, and suggested invariant/policy/test updates for each candidate.",
                "Apply duplicate/noise controls before persistence (score/support/sample thresholds + candidate_key dedupe).",
                "Promotion checklist must include invariant/policy mapping, regression test path, and shadow re-evaluation step.",
            ],
            "refresh_job": "inference.nightly_refit",
            "source_tables": [
                "learning_issue_clusters",
                "extraction_underperforming_classes",
                "unknown_dimension_proposals (status=accepted)",
            ],
            "output_table": "learning_backlog_candidates",
            "run_table": "learning_backlog_bridge_runs",
            "candidate_payload_contract": {
                "required_fields": [
                    "title",
                    "description",
                    "acceptance_criteria[]",
                    "root_cause_hypothesis",
                    "impacted_metrics",
                    "suggested_updates",
                    "promotion_checklist",
                ],
                "approval_required_default": True,
                "status_values": [
                    "candidate",
                    "approved",
                    "dismissed",
                    "promoted",
                ],
                "source_type_values": [
                    "issue_cluster",
                    "extraction_calibration",
                    "unknown_dimension",
                ],
            },
            "guardrails": {
                "cluster_min_score_default": 0.18,
                "cluster_min_events_default": 3,
                "cluster_min_unique_users_default": 2,
                "calibration_min_samples_default": 3,
                "unknown_dimension_min_score_default": 0.18,
                "max_candidates_per_source_default": 6,
                "max_candidates_per_run_default": 12,
                "dedupe_key": "candidate_key (stable hash over source_ref)",
            },
            "promotion_workflow": [
                "candidate_generated",
                "human_approval",
                "invariant_or_policy_update",
                "regression_test_added",
                "shadow_re_evaluation",
            ],
        },
        "unknown_dimension_mining_v1": {
            "rules": [
                "Mine recurring unknown/provisional observation.logged patterns across users.",
                "Cluster by semantic fingerprint + scope context to keep proposals deterministic and auditable.",
                "Emit schema suggestions with value_type/unit/scale hypothesis plus confidence and evidence bundle.",
                "Require explicit human acceptance before routing proposals into backlog bridge.",
                "Retain duplicate/noise controls with support and unique-user thresholds.",
            ],
            "source_event_type": "observation.logged",
            "refresh_job": "inference.nightly_refit",
            "output_table": "unknown_dimension_proposals",
            "run_table": "unknown_dimension_mining_runs",
            "status_values": [
                "candidate",
                "accepted",
                "dismissed",
                "promoted",
            ],
            "schema_suggestion_fields": [
                "name",
                "value_type",
                "expected_unit",
                "expected_scale",
                "description",
            ],
            "defaults": {
                "window_days": 30,
                "min_support": 3,
                "min_unique_users": 2,
                "max_proposals_per_run": 12,
            },
            "backlog_bridge_integration": {
                "route_condition": "status=accepted",
                "target_table": "learning_backlog_candidates",
                "target_source_type": "unknown_dimension",
                "dedupe_key": "proposal_key",
            },
            "approval_workflow": [
                "candidate_review",
                "human_acceptance",
                "contract_draft_validation",
                "backlog_bridge_promotion",
            ],
        },
        "shadow_evaluation_gate_v1": {
            "rules": [
                "Run baseline and candidate replay side-by-side on representative corpus users before rollout.",
                "Compute metric deltas per projection and classify failure classes for both variants.",
                "Stratify shadow replay by model tier (strict, moderate, advanced) and publish pass/fail per tier.",
                "Promotion requires the weakest supported tier to pass (no regression on protected metrics).",
                "Enforce release gate policy on selected metrics with explicit tolerance thresholds.",
                "Do not allow rollout when gate status is fail or insufficient_data.",
            ],
            "entrypoint": "eval_harness.run_shadow_evaluation",
            "inputs": {
                "baseline_config": [
                    "source",
                    "projection_types",
                    "strength_engine",
                    "semantic_top_k",
                    "model_tiers",
                ],
                "candidate_config": [
                    "source",
                    "projection_types",
                    "strength_engine",
                    "semantic_top_k",
                    "model_tiers",
                ],
                "user_ids": "list of corpus users (pseudonymized in report)",
            },
            "release_gate_policy_version": "shadow_eval_gate_v1",
            "tier_matrix_policy_version": "shadow_eval_tier_matrix_v1",
            "weakest_tier": "strict",
            "delta_rules": [
                {
                    "projection_type": "strength_inference",
                    "metric": "coverage_ci95",
                    "direction": "higher_is_better",
                    "max_delta": -0.03,
                },
                {
                    "projection_type": "strength_inference",
                    "metric": "mae",
                    "direction": "lower_is_better",
                    "max_delta": 1.0,
                },
                {
                    "projection_type": "readiness_inference",
                    "metric": "coverage_ci95_nowcast",
                    "direction": "higher_is_better",
                    "max_delta": -0.03,
                },
                {
                    "projection_type": "readiness_inference",
                    "metric": "mae_nowcast",
                    "direction": "lower_is_better",
                    "max_delta": 0.03,
                },
            ],
            "report_sections": [
                "baseline.summary",
                "candidate.summary",
                "metric_deltas",
                "tier_matrix",
                "failure_classes",
                "release_gate",
            ],
        },
        "proof_in_production_v1": {
            "entrypoint": "eval_harness.build_proof_in_production_artifact",
            "script_entrypoint": "scripts/build_proof_in_production_artifact.py",
            "schema_version": "proof_in_production_decision_artifact.v1",
            "required_sections": [
                "decision.status",
                "decision.gate_status",
                "gate.primary_reasons",
                "missing_data",
                "recommended_next_steps",
                "stakeholder_summary",
            ],
            "stakeholder_summary_sections": [
                "headline",
                "decision_status",
                "primary_reasons",
                "missing_data",
                "recommended_next_steps",
            ],
        },
        "visualization_policy": {
            "rules": [
                "Only visualize when policy triggers are present or the user explicitly asks.",
                "Before rendering, provide a visualization_spec with format, purpose, and bound data_sources.",
                "Each data source must resolve to an existing projection reference and optional json_path.",
                "When rich rendering is unavailable, return deterministic ASCII fallback with equivalent meaning.",
                "If quality status is monitor/degraded, label output uncertainty explicitly.",
            ],
            "policy_triggers": [
                "trend",
                "compare",
                "plan_vs_actual",
                "multi_week_scheduling",
            ],
            "preference_override_values": ["auto", "always", "never"],
            "supported_formats": ["chart", "table", "timeline", "ascii", "mermaid"],
            "resolve_endpoint": "/v1/agent/visualization/resolve",
            "telemetry_signal_types": [
                "viz_shown",
                "viz_skipped",
                "viz_source_bound",
                "viz_fallback_used",
                "viz_confusion_signal",
            ],
        },
        "data_correction": {
            "rules": [
                "To correct a wrong event: retract it with event.retracted and "
                "log the correct replacement in the same batch.",
                "Always include retracted_event_type so the system can process "
                "the retraction efficiently.",
                "To clear a profile field, send profile.updated with the field "
                "set to null.",
                "For repair-generated events, include repair_provenance "
                "(source_type, confidence, applies_scope, reason).",
            ],
            "example_batch": [
                {
                    "event_type": "event.retracted",
                    "data": {
                        "retracted_event_id": "01956abc-def0-7000-8000-000000000001",
                        "retracted_event_type": "bodyweight.logged",
                        "reason": "Typo: entered 150kg instead of 85kg",
                    },
                },
                {
                    "event_type": "bodyweight.logged",
                    "data": {
                        "weight_kg": 85.0,
                        "time_of_day": "morning",
                    },
                },
            ],
        },
        "semantic_resolution": {
            "rules": [
                "Prefer exact user aliases first, then semantic candidates from semantic_memory.",
                "If semantic confidence is medium/low, confirm with the user before committing canonical IDs.",
                "Use semantic_memory candidates to create exercise.alias_created for stable future resolution.",
                "For food terms, keep provenance text and attach canonical IDs when confidence is sufficient.",
            ],
            "confidence_bands": {
                "high": ">= 0.86 — safe to apply with inferred confidence",
                "medium": "0.78-0.85 — ask short confirmation",
                "low": "< 0.78 — do not auto-apply",
            },
        },
        "bayesian_inference": {
            "rules": [
                "Treat inference projections as probabilistic guidance, not deterministic truth.",
                "When data is sparse, communicate uncertainty and request more observations.",
                "Use readiness_inference for day-level decision framing, not medical conclusions.",
                "When strength_inference indicates plateau risk, suggest interventions as hypotheses.",
                "Population priors are used only when privacy thresholds are met.",
                "Population prior contribution and usage require explicit user opt-in.",
            ],
            "minimum_data": {
                "strength_inference_points": 3,
                "readiness_inference_days": 5,
            },
            "population_priors": {
                "opt_in_preference_key": "population_priors_opt_in",
                "privacy_gates": {
                    "min_cohort_size": "configurable (default 25)",
                    "window_days": "configurable (default 180)",
                    "storage": "aggregated cohorts only, no per-user artifacts",
                },
            },
        },
        "causal_inference": {
            "rules": [
                "Treat intervention effects as observational estimates, not randomized truth.",
                "Always communicate assumptions and caveats alongside effect sizes.",
                "Use causal outputs for prioritization and hypothesis ranking, not diagnosis.",
                "When overlap is weak or weights are extreme, lower confidence in recommendations.",
            ],
            "minimum_data": {
                "intervention_windows": 24,
                "strength_outcome_windows": 18,
                "minimum_treated_windows": 4,
                "minimum_control_windows": 4,
                "minimum_segment_windows": 12,
            },
            "assumptions": [
                "consistency",
                "no_unmeasured_confounding",
                "positivity",
                "no_interference",
                "model_specification",
            ],
            "caveat_codes": {
                "insufficient_samples": "Not enough treated/control windows to estimate stable effects.",
                "positivity_violation": "Treatment assignment is too deterministic for adjustment.",
                "weak_overlap": "Treated and control propensity distributions overlap weakly.",
                "extreme_weights": "IPW weights are heavy-tailed; point estimates may be unstable.",
                "low_effective_sample_size": "Weighted effective sample size is small.",
                "residual_confounding_risk": "Post-weighting covariate imbalance remains high.",
                "low_outcome_variance": "Outcome variance is small; effect detectability is limited.",
                "wide_interval": "Uncertainty interval is wide; directional claims are fragile.",
                "segment_insufficient_samples": (
                    "A subgroup/phase segment has too few windows for a stable estimate."
                ),
            },
        },
    }


def _get_agent_behavior() -> dict[str, Any]:
    """Return agent behavior guidelines.

    Two layers:
    - vision: the spirit — who the agent is and why. Stands on its own.
    - operational: the rules — how the agent acts in practice.

    User-level overrides (e.g. preferred scope level) live in user_profile,
    not here. This is the system default.
    """
    return {
        "vision": {
            "source": "Joscha Bach, paraphrased",
            "principles": [
                "Complete integrity with the user and with itself.",
                "Explains the user's situation together with them.",
                "The user is free to question everything it does.",
                "It becomes a part of them — not a tool, but an extension of their understanding.",
            ],
        },
        "operational": {
            "scope": {
                "description": "How far the agent goes beyond the explicit request.",
                "default": "strict",
                "levels": {
                    "strict": "Only exactly what was asked. Offer suggestions separately.",
                    "moderate": "Small logical extensions ok, but ask before bigger steps.",
                    "proactive": "Agent may act proactively when context is clear.",
                },
            },
            "rules": [
                "Do only what was explicitly requested — not more.",
                "When ambiguous, ask — don't assume.",
                "When data is missing, ask follow-up questions — don't guess.",
                "When suggesting something beyond the request, frame it as a suggestion, not an action.",
            ],
            "challenge_mode": {
                "schema_version": "challenge_mode.v1",
                "default": "auto",
                "allowed_values": ["auto", "on", "off"],
                "storage_contract": {
                    "event_type": "preference.set",
                    "key": "challenge_mode",
                },
                "discoverability": {
                    "onboarding_hint": (
                        "Challenge Mode ist standardmäßig auf auto aktiv. "
                        "Sag 'Challenge Mode aus', wenn ich weniger challengen soll."
                    ),
                    "intro_marker_key": "challenge_mode_intro_seen",
                    "chat_only_control": True,
                },
                "behavior_matrix": {
                    "auto": (
                        "Challenge only on relevant triggers: high-impact, low confidence, conflicting evidence."
                    ),
                    "on": (
                        "Proactively include at least one risk and one alternative for non-trivial recommendations."
                    ),
                    "off": (
                        "No proactive challenge by default; safety/integrity boundaries remain mandatory."
                    ),
                },
            },
            "user_override_controls_v1": {
                "storage": "user_profile.user.preferences via preference.set",
                "keys": {
                    "autonomy_scope": {
                        "allowed_values": ["strict", "moderate", "proactive"],
                        "default": "moderate",
                        "safety_floor": (
                            "Effective scope is clamped by integrity/calibration status and model-tier limits."
                        ),
                    },
                    "verbosity": {
                        "allowed_values": ["concise", "balanced", "detailed"],
                        "default": "balanced",
                    },
                    "confirmation_strictness": {
                        "allowed_values": ["auto", "always", "never"],
                        "default": "auto",
                        "safety_floor": (
                            "'never' cannot bypass confirm-first requirements from quality/model hard gates."
                        ),
                    },
                },
                "precedence_order": [
                    "workflow + write-proof hard invariants",
                    "quality_health.autonomy_policy",
                    "user_profile preference overrides (within safety floors)",
                    "model_tier policy clamp",
                ],
                "fallback_defaults": {
                    "autonomy_scope": "moderate",
                    "verbosity": "balanced",
                    "confirmation_strictness": "auto",
                },
            },
            "scenario_library_v1": {
                "goal": (
                    "Provide executable behavior scenarios that bind runtime outputs "
                    "to user-visible reliability wording."
                ),
                "required_categories": [
                    "happy_path",
                    "ambiguity",
                    "correction",
                    "contradiction",
                    "low_confidence",
                    "overload",
                    "consistency_prompt",
                ],
                "scenarios": [
                    {
                        "id": "onboarding_logging_saved",
                        "category": "happy_path",
                        "covers_transitions": ["onboarding", "logging"],
                        "model_tier_example": "moderate",
                        "expected_machine_outputs": {
                            "workflow_gate": {"status": "allowed", "phase": "onboarding", "transition": "none"},
                            "claim_guard": {"allow_saved_claim": True, "claim_status": "saved_verified"},
                            "reliability_ux": {"state": "saved"},
                            "save_echo": {
                                "save_echo_required": True,
                                "save_echo_completeness": "complete",
                            },
                            "expected_event_writes": ["quality.save_claim.checked", "learning.signal.logged"],
                        },
                        "expected_user_phrasing": {
                            "label": "Saved",
                            "must_include": ["Saved"],
                            "must_include_values": True,
                            "must_not_include": ["Unresolved", "Inferred"],
                            "clarification_strategy": "none",
                        },
                    },
                    {
                        "id": "planning_override_confirm_first",
                        "category": "ambiguity",
                        "covers_transitions": ["planning_transition", "onboarding_override"],
                        "model_tier_example": "strict",
                        "expected_machine_outputs": {
                            "workflow_gate": {"status": "allowed", "transition": "override", "override_used": True},
                            "autonomy_gate": {"decision": "confirm_first"},
                            "expected_event_writes": ["learning.signal.logged"],
                        },
                        "expected_user_phrasing": {
                            "label": "Saved",
                            "must_include": ["Bestätigung", "Saved"],
                            "must_not_include": ["blocked by unknown reason"],
                            "clarification_strategy": "confirmation_for_high_impact",
                        },
                    },
                    {
                        "id": "correction_inferred_with_provenance",
                        "category": "correction",
                        "covers_transitions": ["correction"],
                        "model_tier_example": "moderate",
                        "expected_machine_outputs": {
                            "reliability_ux": {"state": "inferred"},
                            "expected_event_writes": ["event.retracted", "set.corrected", "learning.signal.logged"],
                        },
                        "expected_user_phrasing": {
                            "label": "Inferred",
                            "must_include": ["Inferred", "Quelle"],
                            "must_not_include": ["Saved ohne Hinweis"],
                            "clarification_strategy": "none_if_provenance_sufficient",
                        },
                    },
                    {
                        "id": "session_feedback_contradiction_unresolved",
                        "category": "contradiction",
                        "covers_transitions": ["logging", "correction"],
                        "model_tier_example": "moderate",
                        "expected_machine_outputs": {
                            "reliability_ux": {"state": "unresolved"},
                            "session_audit": {"status": "needs_clarification"},
                            "expected_event_writes": ["learning.signal.logged"],
                        },
                        "expected_user_phrasing": {
                            "label": "Unresolved",
                            "must_include": ["Unresolved", "Welcher Wert stimmt?"],
                            "must_not_include": ["Saved"],
                            "clarification_strategy": "single_conflict_question",
                        },
                    },
                    {
                        "id": "pending_read_after_write_unresolved",
                        "category": "low_confidence",
                        "covers_transitions": ["logging"],
                        "model_tier_example": "advanced",
                        "expected_machine_outputs": {
                            "claim_guard": {
                                "allow_saved_claim": False,
                                "claim_status": "pending",
                                "uncertainty_markers": ["read_after_write_unverified"],
                            },
                            "reliability_ux": {"state": "unresolved"},
                            "expected_event_writes": ["quality.save_claim.checked", "learning.signal.logged"],
                        },
                        "expected_user_phrasing": {
                            "label": "Unresolved",
                            "must_include": ["Verifikation", "pending"],
                            "must_not_include": ["Saved"],
                            "clarification_strategy": "defer_saved_claim_until_readback",
                        },
                    },
                    {
                        "id": "multi_conflict_overload_single_question",
                        "category": "overload",
                        "covers_transitions": ["logging", "correction", "planning_transition"],
                        "model_tier_example": "strict",
                        "expected_machine_outputs": {
                            "reliability_ux": {"state": "unresolved"},
                            "session_audit": {"status": "needs_clarification"},
                            "expected_event_writes": ["learning.signal.logged"],
                        },
                        "expected_user_phrasing": {
                            "label": "Unresolved",
                            "must_include": ["Konflikt"],
                            "must_not_include": ["mehrere Fragen gleichzeitig"],
                            "clarification_strategy": "one_conflict_only",
                        },
                    },
                    {
                        "id": "proactive_consistency_prompt_one_question",
                        "category": "consistency_prompt",
                        "covers_transitions": ["consistency_review"],
                        "model_tier_example": "moderate",
                        "expected_machine_outputs": {
                            "consistency_inbox": {
                                "requires_human_decision": True,
                                "highest_severity": "warning",
                            },
                            "expected_event_writes": ["quality.consistency.review.decided"],
                        },
                        "expected_user_phrasing": {
                            "label": "Approval-Frage",
                            "must_include": ["Soll ich"],
                            "must_not_include": ["automatisch korrigiert"],
                            "clarification_strategy": "single_approval_question",
                        },
                    },
                ],
            },
            "write_protocol": {
                "required_steps": [
                    "write_with_proof: include idempotency_key per event",
                    "capture durable receipt: event_id + idempotency_key",
                    "read-after-write: verify projection targets before final saved claim",
                ],
                "saved_claim_policy": {
                    "allow_saved_claim_only_if": (
                        "receipt_complete AND read_after_write_verified"
                    ),
                    "otherwise": (
                        "Use deferred language and explicitly state verification is pending."
                    ),
                },
            },
            "save_echo_policy_v1": {
                "schema_version": "save_echo_policy.v1",
                "always_on": True,
                "tier_independent": True,
                "rationale": (
                    "Save-Echo is a data-integrity control, not an autonomy decision. "
                    "It is the only defense against plausible mistranslations (e.g. 60 kg "
                    "instead of 80 kg) that pass all downstream checks (anomaly detection, "
                    "self-healing, replay) undetected. The user's verification of echoed "
                    "values is the sole feedback loop for this failure class."
                ),
                "contract": {
                    "required_after": ["saved_verified", "inferred"],
                    "echo_must_include": (
                        "All user-relevant values that were persisted (exercise, sets, reps, "
                        "weight, duration, etc.). Exact field names are not required; semantic "
                        "coverage of persisted values is."
                    ),
                    "echo_must_not_include": (
                        "Raw technical details (event IDs, idempotency keys, internal timestamps) "
                        "unless the user explicitly requests them."
                    ),
                },
                "message_style": {
                    "mode": "natural_compact",
                    "examples": {
                        "good_minimal": "Bankdrücken 3×8 @ 80 kg — was kommt als nächstes?",
                        "good_conversational": "80 kg Bankdrücken ist drin. Noch die Nebenübungen?",
                        "bad_bureaucratic": (
                            "Event-ID abc123, event_type: set.logged, exercise_id: bench_press, "
                            "sets: 3, reps: 8, weight_kg: 80.0"
                        ),
                        "bad_no_echo": "Alles klar, ist drin. Was noch?",
                    },
                },
                "batch_mode": "compact_summary_allowed",
                "batch_note": (
                    "For batch writes (multiple events), a compact summary covering all "
                    "persisted values is acceptable. Individual per-event echo is not required."
                ),
                "telemetry_fields": {
                    "save_echo_required": "bool — always true when contract applies",
                    "save_echo_present": "bool — whether echo was detected in agent response",
                    "save_echo_completeness": (
                        "'complete' | 'partial' | 'missing' | 'not_assessed' — "
                        "completeness assessment of value coverage (not_assessed is allowed "
                        "at write time before response-level echo analysis)"
                    ),
                },
                "interaction_with_intent_handshake": (
                    "Intent-Handshake (pre-write confirmation) and Save-Echo (post-write "
                    "value mirror) are orthogonal. A strict-tier agent does both: confirms "
                    "before writing AND echoes after. An advanced-tier agent skips confirmation "
                    "but still echoes. The two policies must never be conflated."
                ),
            },
            "reliability_ux_protocol": {
                "goal": (
                    "Prevent certainty inflation by labeling every post-write response as "
                    "saved, inferred, or unresolved."
                ),
                "state_contract": {
                    "saved": {
                        "when": "claim_guard.allow_saved_claim=true AND no unresolved conflicts",
                        "required_message_shape": (
                            "Confirm persistence with receipt/read-after-write basis."
                        ),
                        "must_include": ["state=saved", "assistant_phrase"],
                    },
                    "inferred": {
                        "when": (
                            "write proof verified AND at least one inferred fact or "
                            "deterministic repair provenance exists"
                        ),
                        "required_message_shape": (
                            "State persisted + explicitly mark inferred fields with confidence/provenance."
                        ),
                        "must_include": [
                            "state=inferred",
                            "assistant_phrase",
                            "inferred_facts[]",
                        ],
                    },
                    "unresolved": {
                        "when": (
                            "proof incomplete OR clarification-needed mismatch remains unresolved"
                        ),
                        "required_message_shape": (
                            "Do not claim saved. Ask one conflict-focused clarification question."
                        ),
                        "must_include": [
                            "state=unresolved",
                            "assistant_phrase",
                            "clarification_question",
                        ],
                    },
                },
                "anti_patterns": [
                    "Never say 'saved/logged' when claim_guard.allow_saved_claim=false.",
                    "Never hide inferred values behind certainty wording.",
                    "Never ask broad multi-question prompts when one conflict question is enough.",
                ],
                "clarification_style": {
                    "max_questions_per_turn": 1,
                    "tone": "concise_conflict_focused",
                    "template": "Konflikt bei <scope>: <field> = <option_a>|<option_b>. Welcher Wert stimmt?",
                },
                "compatibility": {
                    "user_override_hooks_must_remain_supported": True,
                    "hooks": [
                        "workflow_gate.override",
                        "autonomy_policy.max_scope_level",
                        "confirmation_template_catalog",
                    ],
                },
            },
            "uncertainty": {
                "low_confidence_fact_policy": (
                    "Use explicit uncertainty markers and deferred labels when confidence or proof is incomplete."
                ),
                "required_markers": [
                    "uncertain",
                    "deferred",
                    "pending_verification",
                ],
            },
            "autonomy_throttling": {
                "source_projection": "quality_health/overview",
                "policy_field": "autonomy_policy",
                "rules": [
                    "When autonomy_policy.throttle_active=true, enforce max_scope_level and require explicit confirmations.",
                    "Treat monitor/degraded SLO status as a hard behavioral boundary, not a suggestion.",
                    "Never escalate autonomy above the policy-defined max_scope_level.",
                ],
                "confirmation_template_catalog": {
                    "healthy": {
                        "non_trivial_action": (
                            "Wenn du willst, kann ich als nächsten Schritt direkt fortfahren."
                        ),
                        "plan_update": (
                            "Wenn du willst, passe ich den Plan jetzt entsprechend an."
                        ),
                    },
                    "monitor": {
                        "non_trivial_action": (
                            "Integritätsstatus ist im Monitor-Bereich. Soll ich mit diesem nächsten Schritt fortfahren?"
                        ),
                        "plan_update": (
                            "Monitor-Status aktiv: Bitte kurz bestätigen, dass ich die Plananpassung durchführen soll."
                        ),
                    },
                    "degraded": {
                        "non_trivial_action": (
                            "Datenintegrität ist aktuell eingeschränkt. Soll ich fortfahren? Bitte antworte mit JA."
                        ),
                        "plan_update": (
                            "Integritätsstatus ist degradiert. Planänderungen brauchen eine explizite Bestätigung. Soll ich den Plan ändern?"
                        ),
                    },
                },
            },
            "consistency_inbox_protocol_v1": {
                "schema_version": "consistency_inbox_protocol.v1",
                "rationale": (
                    "There is no separate UI for backlog approval; the human interacts "
                    "via the AI chat. Proactive consistency findings from nightly analysis "
                    "must surface in the chat and request explicit user decisions before "
                    "any fixes are executed. V1 is safe: no silent auto-fixing."
                ),
                "approval_required_before_fix": True,
                "max_questions_per_turn": 1,
                "allowed_user_decisions": ["approve", "decline", "snooze"],
                "default_snooze_hours": 72,
                "surfacing_rules": [
                    "On next normal chat contact, check consistency_inbox/overview.",
                    "If requires_human_decision=true, surface highest-severity item first.",
                    "Frame as brief observation + one decision question.",
                    "Do not interrupt active training logging for consistency items.",
                ],
                "wording_by_severity": {
                    "critical": (
                        "Short, direct statement of the data issue. "
                        "Example: 'Mir ist aufgefallen, dass bei deinem letzten "
                        "Bankdrücken-Eintrag die Werte nicht zusammenpassen. "
                        "Soll ich das korrigieren?'"
                    ),
                    "warning": (
                        "Casual mention with low urgency. "
                        "Example: 'Kleine Inkonsistenz bei deinen Deadlift-Daten "
                        "— soll ich das anpassen?'"
                    ),
                    "info": (
                        "Optional mention, can be batched. "
                        "Example: 'Ein paar kleine Formatierungsdetails in "
                        "deinen letzten Einträgen — soll ich aufräumen?'"
                    ),
                },
                "cooldown_rules": {
                    "after_decline": "Same item_id not re-prompted for 7 days.",
                    "after_snooze": "Re-prompt after snooze_until timestamp.",
                    "after_approve": "Item removed from inbox after fix applied.",
                    "nagging_protection": (
                        "Max 1 consistency question per chat session. "
                        "If user declines, do not re-ask in the same session."
                    ),
                },
                "decision_event": {
                    "event_type": "quality.consistency.review.decided",
                    "required_fields": [
                        "item_ids",
                        "decision",
                        "decision_source",
                    ],
                    "optional_fields": ["snooze_until"],
                },
                "projection": {
                    "type": "consistency_inbox",
                    "key": "overview",
                    "schema": {
                        "schema_version": "int",
                        "generated_at": "ISO 8601 timestamp",
                        "pending_items_total": "int",
                        "highest_severity": "'critical' | 'warning' | 'info' | 'none'",
                        "requires_human_decision": "bool",
                        "items": [
                            {
                                "item_id": "string (stable, deterministic)",
                                "severity": "'critical' | 'warning' | 'info'",
                                "summary": "string (1-2 sentences, user-facing)",
                                "recommended_action": "string",
                                "evidence_ref": "string (event_id or projection ref)",
                                "first_seen": "ISO 8601 timestamp",
                            }
                        ],
                        "prompt_control": {
                            "last_prompted_at": "ISO 8601 timestamp | null",
                            "snooze_until": "ISO 8601 timestamp | null",
                            "cooldown_active": "bool",
                        },
                    },
                },
                "safety_invariants": [
                    "No fix without explicit user approval in chat.",
                    "User override controls cannot bypass the approval requirement.",
                    "Missing decision defaults to no action (safe).",
                ],
            },
            "security_tiering": {
                "version": "ct3.1",
                "goal": (
                    "Protect agent access paths against prompt exfiltration, API enumeration, "
                    "context scraping, and scope escalation."
                ),
                "default_profile": "default",
                "profile_progression": ["default", "adaptive", "strict"],
                "switch_catalog": {
                    "prompt_hardening": {
                        "owner": "platform_security",
                        "metric": "security.prompt_exfiltration_blocks_rate",
                        "rollout_plan": "baseline now -> adaptive anomaly trigger -> strict manual override",
                    },
                    "api_surface_guard": {
                        "owner": "api_platform",
                        "metric": "security.api_enumeration_blocked_requests",
                        "rollout_plan": "baseline allowlist now -> tighten unknown endpoint budget in adaptive",
                    },
                    "context_minimization": {
                        "owner": "agent_runtime",
                        "metric": "security.context_overshare_incidents",
                        "rollout_plan": "always-on redaction baseline, expand masking + canary in adaptive",
                    },
                    "scope_enforcement": {
                        "owner": "policy_engine",
                        "metric": "security.scope_escalation_prevented_total",
                        "rollout_plan": "read checks now -> strict write scopes + fail-closed in strict",
                    },
                    "abuse_kill_switch": {
                        "owner": "sre_oncall",
                        "metric": "security.kill_switch_time_to_mitigate_seconds",
                        "rollout_plan": "enabled in adaptive and strict only, exercised in game-days weekly",
                    },
                },
                "profiles": {
                    "default": {
                        "intent": "Normal operation with bounded safeguards and observability.",
                        "switches": {
                            "prompt_hardening": "baseline",
                            "api_surface_guard": "allowlist_with_rate_limits",
                            "context_minimization": "redact_secrets_only",
                            "scope_enforcement": "token_scope_match_required",
                            "abuse_kill_switch": "manual_only",
                        },
                        "activation": "System default for healthy tenants.",
                    },
                    "adaptive": {
                        "intent": "Escalate controls when telemetry signals active abuse patterns.",
                        "switches": {
                            "prompt_hardening": "strict_templates_plus_output_filters",
                            "api_surface_guard": "allowlist_plus_anomaly_rate_shaping",
                            "context_minimization": "sensitive_context_allowlist",
                            "scope_enforcement": "per_action_scope_assertions",
                            "abuse_kill_switch": "auto_on_multi_signal_trigger",
                        },
                        "activation": "Triggered when abuse score crosses monitor threshold for 15m.",
                    },
                    "strict": {
                        "intent": "Incident mode with fail-closed behavior and minimal context surface.",
                        "switches": {
                            "prompt_hardening": "locked_system_prompt_and_no_tool_reflection",
                            "api_surface_guard": "hard_allowlist_and_low_burst_limits",
                            "context_minimization": "need_to_know_projection_subset",
                            "scope_enforcement": "write_block_except_break_glass",
                            "abuse_kill_switch": "always_armed_with_oncall_approval",
                        },
                        "activation": "Manual incident response or repeated adaptive breaches.",
                    },
                },
                "threat_matrix": [
                    {
                        "threat_id": "TM-001",
                        "name": "prompt_exfiltration",
                        "attacker_goal": "Reveal hidden prompts, secrets, or policy internals.",
                        "attack_path": (
                            "Nested instruction payloads attempt jailbreak + reflection from user input."
                        ),
                        "detection_signals": [
                            "Prompt leak regex hit",
                            "Unexpected tool schema exposure",
                            "High prompt_reflection_ratio",
                        ],
                        "controls": {
                            "default": ["prompt_hardening"],
                            "adaptive": ["prompt_hardening", "context_minimization"],
                            "strict": ["prompt_hardening", "context_minimization", "abuse_kill_switch"],
                        },
                        "owner": "platform_security",
                        "metric": "security.prompt_exfiltration_attempts_blocked",
                        "rollout_plan": "start now in default, auto-escalate to adaptive, strict via incident cmd",
                    },
                    {
                        "threat_id": "TM-002",
                        "name": "api_enumeration",
                        "attacker_goal": "Discover hidden endpoints and broaden attack surface.",
                        "attack_path": "Iterate endpoint patterns, scopes, and malformed tool calls.",
                        "detection_signals": [
                            "404/403 sweep burst",
                            "Unknown endpoint entropy spike",
                            "Repeated scope denial from same principal",
                        ],
                        "controls": {
                            "default": ["api_surface_guard"],
                            "adaptive": ["api_surface_guard", "scope_enforcement"],
                            "strict": ["api_surface_guard", "scope_enforcement", "abuse_kill_switch"],
                        },
                        "owner": "api_platform",
                        "metric": "security.api_enumeration_attempt_rate",
                        "rollout_plan": "baseline now, anomaly shaping in adaptive, strict hard caps in incidents",
                    },
                    {
                        "threat_id": "TM-003",
                        "name": "context_scraping",
                        "attacker_goal": "Extract user context outside requested task scope.",
                        "attack_path": "Prompt asks for broad summaries to elicit unrelated private context.",
                        "detection_signals": [
                            "Large context chunk retrieval",
                            "Cross-dimension query fan-out",
                            "Response includes unrequested sensitive keys",
                        ],
                        "controls": {
                            "default": ["context_minimization"],
                            "adaptive": ["context_minimization", "scope_enforcement"],
                            "strict": ["context_minimization", "scope_enforcement", "abuse_kill_switch"],
                        },
                        "owner": "agent_runtime",
                        "metric": "security.context_leak_prevented_total",
                        "rollout_plan": "redaction baseline now, adaptive allowlists next, strict subset in incidents",
                    },
                    {
                        "threat_id": "TM-004",
                        "name": "scope_escalation",
                        "attacker_goal": "Execute writes/actions beyond granted authority.",
                        "attack_path": (
                            "Forge or replay elevated scopes via tool invocations and policy bypass attempts."
                        ),
                        "detection_signals": [
                            "Scope mismatch failures",
                            "Idempotency replay with scope change",
                            "Write attempt during throttle boundary",
                        ],
                        "controls": {
                            "default": ["scope_enforcement"],
                            "adaptive": ["scope_enforcement", "api_surface_guard"],
                            "strict": ["scope_enforcement", "api_surface_guard", "abuse_kill_switch"],
                        },
                        "owner": "policy_engine",
                        "metric": "security.scope_escalation_denied_total",
                        "rollout_plan": "enforce read/write parity now, strict fail-closed writes by incident playbook",
                    },
                ],
            },
        },
    }


def build_dimensions(dimension_metadata: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build the dimensions section from registry declarations.

    Strips non-serializable fields (manifest_contribution callable).
    Includes context_seeds for interview guidance (Decision 8).
    """
    dimensions = {}
    for name, meta in dimension_metadata.items():
        entry: dict[str, Any] = {
            "description": meta.get("description", ""),
            "key_structure": meta.get("key_structure", ""),
            "projection_key": meta.get("projection_key", "overview"),
            "granularity": meta.get("granularity", []),
            "event_types": meta.get("event_types", []),
            "relates_to": meta.get("relates_to", {}),
        }
        if "context_seeds" in meta:
            entry["context_seeds"] = meta["context_seeds"]
        if "output_schema" in meta:
            entry["output_schema"] = meta["output_schema"]
        dimensions[name] = entry
    return dimensions


def _get_projection_schemas() -> dict[str, Any]:
    """Output schemas for non-dimension projections (user_profile, custom).

    Domain dimensions declare output_schema in their dimension_meta and appear
    in the 'dimensions' section. These projections don't have dimension_meta
    but agents still need to know their structure.
    """
    return {
        "user_profile": {
            "projection_key": "me",
            "description": "User identity, preferences, data quality, and agent agenda",
            "output_schema": {
                "user": {
                    "aliases": {"<alias>": {"target": "string — canonical exercise_id", "confidence": "string — confirmed|inferred"}},
                    "preferences": {"<key>": "any"},
                    "goals": ["object — goal-specific fields"],
                    "profile": "object or null — accumulated profile.updated fields",
                    "injuries": ["object — injury reports (optional)"],
                    "dimensions": {
                        "<dimension_name>": {
                            "status": "string — active|no_data",
                            "freshness": "ISO 8601 datetime (if active)",
                            "coverage": {"from": "ISO 8601 date", "to": "ISO 8601 date"},
                        },
                    },
                    "observed_patterns": {
                        "observed_fields": {"<event_type>": {"<field>": {"count": "integer", "dimensions": ["string"]}}},
                        "orphaned_event_types": {"<event_type>": {"count": "integer", "common_fields": ["string"]}},
                    },
                    "data_quality": {
                        "total_set_logged_events": "integer",
                        "events_without_exercise_id": "integer",
                        "actionable": [{"type": "string — unresolved_exercise|unconfirmed_alias", "exercise": "string", "occurrences": "integer"}],
                        "orphaned_event_types": [{"event_type": "string", "count": "integer"}],
                    },
                    "interview_coverage": [{"area": "string", "status": "string — covered|uncovered|needs_depth"}],
                },
                "agenda": [{
                    "priority": "string — high|medium|low|info",
                    "type": "string — onboarding_needed|profile_refresh_suggested|resolve_exercises|confirm_alias|field_observed|orphaned_event_type",
                    "detail": "string",
                    "dimensions": ["string"],
                }],
            },
        },
        "custom": {
            "description": "Agent-created custom projections (Decision 10, Phase 3)",
            "projection_key": "<rule_name>",
            "patterns": {
                "field_tracking": {
                    "output_schema": {
                        "rule": "object — the projection_rule.created event data",
                        "recent_entries": [{"date": "ISO 8601 date", "<field>": "number — daily average"}],
                        "weekly_summary": [{"week": "ISO 8601 week", "entries": "integer", "<field>_avg": "number"}],
                        "all_time": {"<field>": {"avg": "number", "min": "number", "max": "number", "count": "integer"}},
                        "data_quality": {"total_events_processed": "integer", "fields_present": {"<field>": "integer"}},
                    },
                },
                "categorized_tracking": {
                    "output_schema": {
                        "rule": "object — the projection_rule.created event data",
                        "categories": {
                            "<category>": {
                                "count": "integer",
                                "recent_entries": [{"timestamp": "ISO 8601 datetime", "<field>": "any"}],
                                "fields": {"<field>": {"avg": "number", "min": "number", "max": "number"}},
                            },
                        },
                        "data_quality": {"total_events_processed": "integer", "categories_found": "integer"},
                    },
                },
            },
        },
    }


def build_system_config() -> dict[str, Any]:
    """Build the complete system config from all registered sources.

    This is deployment-static: same output for same code version.
    """
    dimension_metadata = get_dimension_metadata()
    return {
        "dimensions": build_dimensions(dimension_metadata),
        "event_conventions": get_event_conventions(),
        "conventions": _get_conventions(),
        "time_conventions": {
            "week": "ISO 8601 (2026-W06)",
            "date": "ISO 8601 (2026-02-08)",
            "timestamp": "ISO 8601 with timezone",
        },
        "interview_guide": get_interview_guide(),
        "agent_behavior": _get_agent_behavior(),
        "projection_schemas": _get_projection_schemas(),
    }


async def ensure_system_config(conn: psycopg.AsyncConnection[Any]) -> None:
    """Write system_config to DB. Called once on worker startup.

    Uses UPSERT — safe to call multiple times. Version increments
    on each write so clients can detect staleness.
    """
    data = build_system_config()

    await conn.execute(
        """
        INSERT INTO system_config (key, data, version, updated_at)
        VALUES ('global', %s, 1, NOW())
        ON CONFLICT (key) DO UPDATE SET
            data = EXCLUDED.data,
            version = system_config.version + 1,
            updated_at = NOW()
        """,
        (Json(data),),
    )
    await conn.commit()
    logger.info("System config written (dimensions=%d, event_conventions=%d)",
                len(data["dimensions"]), len(data["event_conventions"]))
