from __future__ import annotations

from pathlib import Path

import kura_workers.handlers  # noqa: F401 - register projection metadata
from kura_workers.registry import get_dimension_metadata

CAUSAL_HANDLER = Path("workers/src/kura_workers/handlers/causal_inference.py")


def test_causal_dimension_declares_supplement_evidence_overlay_schema() -> None:
    metadata = get_dimension_metadata()
    causal = metadata["causal_inference"]
    interventions_schema = causal["output_schema"]["interventions"]["<intervention_name>"]
    overlay_schema = interventions_schema["evidence_overlay"]
    assert overlay_schema["schema_version"].startswith("supplement_evidence_overlay.v1")
    assert overlay_schema["policy_role"] == "advisory_only"
    assert "strong_observational" in overlay_schema["tier"]


def test_supplement_overlay_is_explicitly_advisory_only() -> None:
    src = CAUSAL_HANDLER.read_text(encoding="utf-8")
    assert "SUPPLEMENT_EVIDENCE_OVERLAY_SCHEMA_VERSION" in src
    assert "Observational evidence tier only" in src
    assert "intervention_payload[\"evidence_overlay\"] = _build_supplement_evidence_overlay(" in src

