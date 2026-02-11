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
        "data_correction": {
            "rules": [
                "To correct a wrong event: retract it with event.retracted and "
                "log the correct replacement in the same batch.",
                "Always include retracted_event_type so the system can process "
                "the retraction efficiently.",
                "To clear a profile field, send profile.updated with the field "
                "set to null.",
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
