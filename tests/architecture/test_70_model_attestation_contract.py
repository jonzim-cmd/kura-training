from __future__ import annotations

from pathlib import Path


AGENT_RS = Path(__file__).resolve().parents[2] / "api" / "src" / "routes" / "agent.rs"


def test_model_attestation_contract_markers_exist() -> None:
    src = AGENT_RS.read_text(encoding="utf-8")
    assert "struct AgentModelAttestation" in src
    assert "MODEL_ATTESTATION_SCHEMA_VERSION" in src
    assert "KURA_AGENT_MODEL_ATTESTATION_SECRET" in src
    assert "verify_model_attestation" in src
    assert "resolve_model_identity_for_write" in src
    assert "runtime_model_identity" in src


def test_model_attestation_fallback_and_reason_codes_are_explicit() -> None:
    src = AGENT_RS.read_text(encoding="utf-8")
    assert "model_attestation_missing_fallback" in src
    assert "model_attestation_invalid_signature" in src
    assert "model_attestation_stale" in src
    assert "model_attestation_replayed" in src
    assert "model_identity_unknown_fallback_strict" in src


def test_auto_tiering_guardrails_have_min_samples_and_hysteresis() -> None:
    src = AGENT_RS.read_text(encoding="utf-8")
    assert "MODEL_TIER_AUTO_MIN_SAMPLES" in src
    assert "apply_model_tier_hysteresis" in src
    assert "resolve_auto_tier_policy_for_attested_model" in src
    assert "MODEL_TIER_AUTO_LOW_SAMPLES_CONFIRM_REASON_CODE" in src
