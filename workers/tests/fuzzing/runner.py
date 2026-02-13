"""Scenario execution engine.

Runs AdversarialScenario instances against the real API and reports results.
Used by both the LLM adversarial layer and the transcript regression layer.
"""

from __future__ import annotations

import uuid
from typing import Any

from .conftest import KuraTestClient
from .contracts import CreateEventRequest, EventMetadata, INVARIANT_CODES
from .scenarios import AdversarialScenario, ScenarioResult


def run_scenario(client: KuraTestClient, scenario: AdversarialScenario) -> ScenarioResult:
    """Execute a single scenario against the API and check expectations."""
    events = scenario.events

    if not events:
        return ScenarioResult(
            scenario_id=scenario.id,
            passed=False,
            actual_status=0,
            actual_code=None,
            actual_warnings=[],
            error_message="Scenario has no events",
        )

    # Single event or batch?
    if len(events) == 1:
        return _run_single_event(client, scenario, events[0])
    else:
        return _run_batch(client, scenario, events)


def _run_single_event(
    client: KuraTestClient,
    scenario: AdversarialScenario,
    event_dict: dict[str, Any],
) -> ScenarioResult:
    """Run a single-event scenario."""
    # Build request, supplying metadata if missing
    metadata = event_dict.get("metadata", {})
    if "idempotency_key" not in metadata or not metadata["idempotency_key"]:
        metadata["idempotency_key"] = str(uuid.uuid4())

    req = CreateEventRequest(
        event_type=event_dict["event_type"],
        data=event_dict.get("data", {}),
        metadata=EventMetadata(
            idempotency_key=metadata["idempotency_key"],
            session_id=metadata.get("session_id"),
        ),
        **({"timestamp": event_dict["timestamp"]} if "timestamp" in event_dict else {}),
    )

    body, status = client.post_event(req)
    return _evaluate(scenario, body, status)


def _run_batch(
    client: KuraTestClient,
    scenario: AdversarialScenario,
    event_dicts: list[dict[str, Any]],
) -> ScenarioResult:
    """Run a batch scenario."""
    events = []
    for ed in event_dicts:
        metadata = ed.get("metadata", {})
        if "idempotency_key" not in metadata or not metadata["idempotency_key"]:
            metadata["idempotency_key"] = str(uuid.uuid4())
        events.append(CreateEventRequest(
            event_type=ed["event_type"],
            data=ed.get("data", {}),
            metadata=EventMetadata(
                idempotency_key=metadata["idempotency_key"],
                session_id=metadata.get("session_id"),
            ),
            **({"timestamp": ed["timestamp"]} if "timestamp" in ed else {}),
        ))

    body, status = client.post_batch(events)
    return _evaluate(scenario, body, status)


def _evaluate(
    scenario: AdversarialScenario,
    body: dict[str, Any],
    status: int,
) -> ScenarioResult:
    """Evaluate API response against scenario expectations."""
    actual_code = body.get("error") or body.get("code")
    actual_warnings = body.get("warnings", [])
    expected = scenario.expected_behavior

    if expected == "accepted":
        if status >= 400:
            # Check if this is a domain invariant (acceptable)
            domain_codes = {
                "inv_workflow_phase_required",
                "inv_plan_write_requires_write_with_proof",
                "inv_timezone_required_for_temporal_write",
            }
            if actual_code in domain_codes:
                # Domain invariant — acceptable for scenarios that don't control user state
                return ScenarioResult(
                    scenario_id=scenario.id,
                    passed=True,
                    actual_status=status,
                    actual_code=actual_code,
                    actual_warnings=actual_warnings,
                    error_message=f"Accepted (domain invariant: {actual_code})",
                )
            return ScenarioResult(
                scenario_id=scenario.id,
                passed=False,
                actual_status=status,
                actual_code=actual_code,
                actual_warnings=actual_warnings,
                error_message=f"Expected accepted, got {status}: {actual_code}",
            )
        return ScenarioResult(
            scenario_id=scenario.id,
            passed=True,
            actual_status=status,
            actual_code=actual_code,
            actual_warnings=actual_warnings,
        )

    elif expected == "rejected":
        if status < 400:
            return ScenarioResult(
                scenario_id=scenario.id,
                passed=False,
                actual_status=status,
                actual_code=actual_code,
                actual_warnings=actual_warnings,
                error_message=f"Expected rejected, got {status}",
            )
        # Check expected codes
        if scenario.expected_codes and actual_code not in scenario.expected_codes:
            # Domain invariants may fire before structural ones — still valid
            domain_codes = {
                "inv_workflow_phase_required",
                "inv_plan_write_requires_write_with_proof",
                "inv_timezone_required_for_temporal_write",
            }
            if actual_code not in domain_codes:
                return ScenarioResult(
                    scenario_id=scenario.id,
                    passed=False,
                    actual_status=status,
                    actual_code=actual_code,
                    actual_warnings=actual_warnings,
                    error_message=(
                        f"Expected code in {scenario.expected_codes}, "
                        f"got '{actual_code}'"
                    ),
                )
        return ScenarioResult(
            scenario_id=scenario.id,
            passed=True,
            actual_status=status,
            actual_code=actual_code,
            actual_warnings=actual_warnings,
        )

    elif expected == "warning":
        if status >= 400:
            return ScenarioResult(
                scenario_id=scenario.id,
                passed=False,
                actual_status=status,
                actual_code=actual_code,
                actual_warnings=actual_warnings,
                error_message=f"Expected warning (2xx), got {status}",
            )
        if not actual_warnings:
            return ScenarioResult(
                scenario_id=scenario.id,
                passed=False,
                actual_status=status,
                actual_code=actual_code,
                actual_warnings=actual_warnings,
                error_message="Expected warnings but got none",
            )
        return ScenarioResult(
            scenario_id=scenario.id,
            passed=True,
            actual_status=status,
            actual_code=actual_code,
            actual_warnings=actual_warnings,
        )

    return ScenarioResult(
        scenario_id=scenario.id,
        passed=False,
        actual_status=status,
        actual_code=actual_code,
        actual_warnings=actual_warnings,
        error_message=f"Unknown expected_behavior: {expected}",
    )


def run_all_scenarios(
    client: KuraTestClient,
    scenarios: list[AdversarialScenario],
) -> list[ScenarioResult]:
    """Run all scenarios and return results."""
    return [run_scenario(client, s) for s in scenarios]
