from __future__ import annotations

from kura_workers import eval_harness
from kura_workers.handlers import causal_inference, inference_nightly, readiness_inference
from kura_workers.inference_event_registry import (
    CAUSAL_SIGNAL_EVENT_TYPES,
    EVAL_CAUSAL_EVENT_TYPES,
    EVAL_READINESS_EVENT_TYPES,
    NIGHTLY_REFIT_TRIGGER_EVENT_TYPES,
    READINESS_SIGNAL_EVENT_TYPES,
)
from kura_workers.registry import get_dimension_metadata


def test_inference_event_registry_declares_required_canonical_coverage() -> None:
    readiness = set(READINESS_SIGNAL_EVENT_TYPES)
    causal = set(CAUSAL_SIGNAL_EVENT_TYPES)
    nightly = set(NIGHTLY_REFIT_TRIGGER_EVENT_TYPES)

    assert {"set.logged", "session.logged", "set.corrected", "external.activity_imported"} <= readiness
    assert {"session.logged", "set.corrected", "exercise.alias_created"} <= causal
    assert {"session.logged", "set.corrected", "external.activity_imported"} <= nightly
    assert set(READINESS_SIGNAL_EVENT_TYPES) <= set(EVAL_READINESS_EVENT_TYPES)
    assert set(CAUSAL_SIGNAL_EVENT_TYPES) <= set(EVAL_CAUSAL_EVENT_TYPES)


def test_inference_paths_reference_registry_objects_directly() -> None:
    assert readiness_inference.READINESS_SIGNAL_EVENT_TYPES is READINESS_SIGNAL_EVENT_TYPES
    assert causal_inference.CAUSAL_SIGNAL_EVENT_TYPES is CAUSAL_SIGNAL_EVENT_TYPES
    assert eval_harness.EVAL_READINESS_EVENT_TYPES is EVAL_READINESS_EVENT_TYPES
    assert eval_harness.EVAL_CAUSAL_EVENT_TYPES is EVAL_CAUSAL_EVENT_TYPES
    assert (
        inference_nightly.NIGHTLY_REFIT_TRIGGER_EVENT_TYPES
        is NIGHTLY_REFIT_TRIGGER_EVENT_TYPES
    )


def test_projection_dimension_metadata_matches_registry_contracts() -> None:
    metadata = get_dimension_metadata()
    readiness_meta = metadata["readiness_inference"]
    causal_meta = metadata["causal_inference"]
    assert readiness_meta["event_types"] == list(READINESS_SIGNAL_EVENT_TYPES)
    assert causal_meta["event_types"] == list(CAUSAL_SIGNAL_EVENT_TYPES)
