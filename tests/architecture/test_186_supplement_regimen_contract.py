"""Supplement regimen contract (native lifecycle + inference linkage).

Why:
Users should not have to manually log the same supplement every day.
The system must support lifecycle semantics (set/pause/resume/stop) and
daily exceptions (taken/skipped) as first-class events, with explicit policy
registration and downstream inference visibility.
"""

from __future__ import annotations

from pathlib import Path

import kura_workers.handlers  # noqa: F401
from kura_workers.event_conventions import get_event_conventions
from kura_workers.inference_event_registry import (
    CAUSAL_SIGNAL_EVENT_TYPES,
    NIGHTLY_REFIT_TRIGGER_EVENT_TYPES,
)
from kura_workers.registry import get_dimension_metadata


AGENT_EVENT_TYPE_POLICY = Path("api/src/routes/agent/event_type_policy.rs")
AGENT_ROUTE = Path("api/src/routes/agent.rs")


def test_event_conventions_define_native_supplement_lifecycle_events() -> None:
    conventions = get_event_conventions()
    required = {
        "supplement.regimen.set",
        "supplement.regimen.paused",
        "supplement.regimen.resumed",
        "supplement.regimen.stopped",
        "supplement.taken",
        "supplement.skipped",
        "supplement.logged",
    }
    assert required <= set(conventions.keys())
    assert "name" in conventions["supplement.regimen.set"]["fields"]
    assert "duration_days" in conventions["supplement.regimen.paused"]["fields"]
    assert "effective_date" in conventions["supplement.regimen.resumed"]["fields"]
    assert "date" in conventions["supplement.skipped"]["fields"]


def test_supplements_dimension_metadata_declares_cross_dimension_linkage() -> None:
    metadata = get_dimension_metadata()
    supplements = metadata["supplements"]
    assert supplements["projection_key"] == "overview"
    relates_to = supplements["relates_to"]
    assert "recovery" in relates_to
    assert "nutrition" in relates_to
    assert "training_timeline" in relates_to
    assert "causal_inference" in relates_to


def test_supplement_events_are_registered_for_causal_and_nightly_recompute() -> None:
    causal = set(CAUSAL_SIGNAL_EVENT_TYPES)
    nightly = set(NIGHTLY_REFIT_TRIGGER_EVENT_TYPES)
    required = {
        "supplement.regimen.set",
        "supplement.regimen.paused",
        "supplement.regimen.resumed",
        "supplement.regimen.stopped",
        "supplement.taken",
        "supplement.skipped",
    }
    assert required <= causal
    assert required <= nightly


def test_agent_formal_event_type_policy_and_context_surface_include_supplements() -> None:
    policy_src = AGENT_EVENT_TYPE_POLICY.read_text(encoding="utf-8")
    for event_type in (
        "supplement.regimen.set",
        "supplement.regimen.paused",
        "supplement.regimen.resumed",
        "supplement.regimen.stopped",
        "supplement.taken",
        "supplement.skipped",
    ):
        assert f"\"{event_type}\"" in policy_src

    route_src = AGENT_ROUTE.read_text(encoding="utf-8")
    assert "normalized.starts_with(\"supplement.\")" in route_src
    assert "fetch_projection(&mut tx, user_id, \"supplements\", \"overview\")" in route_src
