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
from .interview_guide import get_interview_guide
from .registry import get_dimension_metadata
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
            "required_fields": [
                "dimension",
                "value",
                "confidence",
                "context_text",
                "provenance",
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
