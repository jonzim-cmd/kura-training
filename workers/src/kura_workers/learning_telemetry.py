"""Canonical learning telemetry helpers (Decision 13 + Continuous Learning Loop).

This module defines a stable schema and signal taxonomy for implicit learning
signals so events can be clustered across users/sessions/agents without
storing raw user identifiers in telemetry payloads.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any

LEARNING_TELEMETRY_SCHEMA_VERSION = 1
_DEFAULT_TELEMETRY_SALT = "kura-learning-telemetry-v1"
_DEFAULT_AGENT_VERSION = "unknown"

_SIGNAL_CATEGORIES: dict[str, str] = {
    "quality_issue_detected": "quality_signal",
    "repair_proposed": "quality_signal",
    "repair_simulated_safe": "quality_signal",
    "repair_simulated_risky": "quality_signal",
    "repair_auto_applied": "quality_signal",
    "repair_auto_rejected": "quality_signal",
    "repair_verified_closed": "quality_signal",
    "save_handshake_verified": "outcome_signal",
    "save_handshake_pending": "friction_signal",
    "save_claim_mismatch_attempt": "friction_signal",
    "workflow_violation": "friction_signal",
    "workflow_phase_transition_closed": "outcome_signal",
    "workflow_override_used": "correction_signal",
    "viz_shown": "outcome_signal",
    "viz_skipped": "outcome_signal",
    "viz_source_bound": "quality_signal",
    "viz_fallback_used": "friction_signal",
    "viz_confusion_signal": "friction_signal",
    "response_mode_selected": "outcome_signal",
    "personal_failure_profile_observed": "quality_signal",
    "retrieval_regret_observed": "friction_signal",
    "laaj_sidecar_assessed": "quality_signal",
    "post_task_reflection_confirmed": "outcome_signal",
    "post_task_reflection_partial": "friction_signal",
    "post_task_reflection_unresolved": "friction_signal",
    "correction_applied": "correction_signal",
    "correction_undone": "correction_signal",
    "clarification_requested": "friction_signal",
}


def core_signal_types() -> tuple[str, ...]:
    """Return the stable, documented signal catalog."""
    return tuple(sorted(_SIGNAL_CATEGORIES.keys()))


def signal_category(signal_type: str) -> str:
    """Return category for a known signal type."""
    normalized = str(signal_type).strip()
    if normalized not in _SIGNAL_CATEGORIES:
        raise ValueError(f"Unknown learning telemetry signal_type: {signal_type!r}")
    return _SIGNAL_CATEGORIES[normalized]


def pseudonymize_user_id(user_id: str) -> str:
    """Return stable pseudonymous user reference for telemetry aggregation."""
    salt = os.getenv("KURA_TELEMETRY_SALT", _DEFAULT_TELEMETRY_SALT)
    raw = f"{salt}:{str(user_id).strip()}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return f"u_{digest[:24]}"


def normalize_confidence_band(value: Any) -> str:
    """Normalize confidence to low|medium|high."""
    if isinstance(value, (int, float)):
        score = float(value)
        if score >= 0.86:
            return "high"
        if score >= 0.6:
            return "medium"
        return "low"

    text = str(value or "").strip().lower()
    if text in {"high", "medium", "low"}:
        return text
    if text in {"confirmed", "user_confirmed", "explicit"}:
        return "high"
    if text in {"inferred", "estimated"}:
        return "medium"
    return "low"


def _stable_suffix(seed: str) -> str:
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]


def build_learning_signal_event(
    *,
    user_id: str,
    signal_type: str,
    workflow_phase: str,
    source: str,
    agent: str,
    modality: str = "chat",
    confidence: Any = "medium",
    issue_type: str | None = None,
    invariant_id: str | None = None,
    attributes: dict[str, Any] | None = None,
    session_id: str | None = None,
    timestamp: str | datetime | None = None,
    idempotency_seed: str | None = None,
    agent_version: str | None = None,
) -> dict[str, Any]:
    """Build one learning.signal.logged event payload."""
    category = signal_category(signal_type)
    confidence_band = normalize_confidence_band(confidence)
    captured_at = (
        timestamp.isoformat()
        if isinstance(timestamp, datetime)
        else str(timestamp)
        if timestamp
        else datetime.now(timezone.utc).isoformat()
    )
    pseudo_user = pseudonymize_user_id(str(user_id))
    signature = {
        "issue_type": issue_type or "none",
        "invariant_id": invariant_id or "none",
        "agent_version": (
            agent_version
            or os.getenv("KURA_AGENT_VERSION", _DEFAULT_AGENT_VERSION)
        ),
        "workflow_phase": str(workflow_phase or "unknown").strip() or "unknown",
        "modality": str(modality or "unknown").strip() or "unknown",
        "confidence_band": confidence_band,
    }
    signature_seed = "|".join(
        [
            signal_type,
            signature["issue_type"],
            signature["invariant_id"],
            signature["agent_version"],
            signature["workflow_phase"],
            signature["modality"],
            signature["confidence_band"],
        ]
    )
    cluster_signature = f"ls_{_stable_suffix(signature_seed)}"
    seed = idempotency_seed or f"{signal_type}:{cluster_signature}:{captured_at}"
    idempotency_key = f"learning-signal-{_stable_suffix(seed)}"

    return {
        "timestamp": captured_at,
        "event_type": "learning.signal.logged",
        "data": {
            "schema_version": LEARNING_TELEMETRY_SCHEMA_VERSION,
            "signal_type": signal_type,
            "category": category,
            "captured_at": captured_at,
            "user_ref": {"pseudonymized_user_id": pseudo_user},
            "signature": signature,
            "cluster_signature": cluster_signature,
            "attributes": attributes or {},
        },
        "metadata": {
            "source": source,
            "agent": agent,
            "session_id": session_id or "learning:telemetry",
            "idempotency_key": idempotency_key,
        },
    }
