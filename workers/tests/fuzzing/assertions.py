"""Invariant assertion helpers for fuzzing tests.

These functions check API responses against known invariant properties.
They don't replicate Rust validation — they assert observable behavior
from the API response.
"""

from __future__ import annotations

from typing import Any

from .contracts import (
    INVARIANT_CODES,
    PLAUSIBILITY_RANGES,
)


class InvariantViolation(AssertionError):
    """An API invariant was violated."""

    def __init__(self, invariant: str, message: str, context: dict[str, Any] | None = None):
        self.invariant = invariant
        self.context = context or {}
        super().__init__(f"[{invariant}] {message}")


def assert_success(response: dict[str, Any], status_code: int) -> None:
    """Assert a 2xx response."""
    if status_code >= 400:
        raise InvariantViolation(
            "expected_success",
            f"Expected 2xx, got {status_code}: {response}",
            {"status": status_code, "body": response},
        )


def assert_policy_violation(
    response: dict[str, Any],
    status_code: int,
    *,
    expected_code: str | None = None,
) -> str:
    """Assert a 422 policy violation and return the violation code."""
    if status_code != 422:
        raise InvariantViolation(
            "expected_policy_violation",
            f"Expected 422, got {status_code}: {response}",
            {"status": status_code, "body": response},
        )

    code = response.get("error") or response.get("code", "")
    if not code:
        raise InvariantViolation(
            "policy_violation_missing_code",
            f"Policy violation response missing 'error'/'code': {response}",
            {"body": response},
        )

    if code not in INVARIANT_CODES:
        raise InvariantViolation(
            "unknown_invariant_code",
            f"Unknown invariant code '{code}' — add to contracts.INVARIANT_CODES",
            {"code": code, "body": response},
        )

    if expected_code is not None and code != expected_code:
        raise InvariantViolation(
            "wrong_invariant_code",
            f"Expected code '{expected_code}', got '{code}'",
            {"expected": expected_code, "actual": code, "body": response},
        )

    # Verify self-correcting error structure
    _assert_self_correcting_error(response)

    return code


def assert_validation_error(response: dict[str, Any], status_code: int) -> None:
    """Assert a 400 validation error."""
    if status_code != 400:
        raise InvariantViolation(
            "expected_validation_error",
            f"Expected 400, got {status_code}: {response}",
            {"status": status_code, "body": response},
        )


def assert_rejection(response: dict[str, Any], status_code: int) -> str | None:
    """Assert that the request was rejected (400 or 422). Return code if 422."""
    if status_code < 400:
        raise InvariantViolation(
            "expected_rejection",
            f"Expected rejection (4xx), got {status_code}",
            {"status": status_code, "body": response},
        )
    if status_code == 422:
        return assert_policy_violation(response, status_code)
    return None


def _assert_self_correcting_error(response: dict[str, Any]) -> None:
    """Every policy violation must have agent-first error fields."""
    # API uses 'error' as the code field, plus 'message'
    if "error" not in response and "code" not in response:
        raise InvariantViolation(
            "self_correcting_error_incomplete",
            f"Policy violation missing 'error' field: {response}",
            {"body": response},
        )
    if "message" not in response:
        raise InvariantViolation(
            "self_correcting_error_incomplete",
            f"Policy violation missing 'message' field: {response}",
            {"body": response},
        )

    # docs_hint should be present for agent self-correction
    if "docs_hint" not in response:
        raise InvariantViolation(
            "self_correcting_error_no_docs_hint",
            f"Policy violation missing 'docs_hint': {response}",
            {"body": response},
        )


def assert_plausibility_warnings(
    response: dict[str, Any],
    event_type: str,
    data: dict[str, Any],
) -> None:
    """Assert that out-of-range values produce plausibility warnings."""
    ranges = PLAUSIBILITY_RANGES.get(event_type, {})
    warnings = response.get("warnings", [])
    warning_fields = {w.get("field", "") for w in warnings}

    for field_name, (min_v, max_v) in ranges.items():
        value = data.get(field_name)
        if value is None or not isinstance(value, (int, float)):
            continue
        if value < min_v or value > max_v:
            if field_name not in warning_fields:
                raise InvariantViolation(
                    "missing_plausibility_warning",
                    f"Expected warning for {field_name}={value} outside [{min_v}, {max_v}]",
                    {"event_type": event_type, "field": field_name, "value": value},
                )


def assert_no_plausibility_warnings(
    response: dict[str, Any],
    event_type: str,
    data: dict[str, Any],
) -> None:
    """Assert that in-range values do NOT produce plausibility warnings."""
    ranges = PLAUSIBILITY_RANGES.get(event_type, {})
    warnings = response.get("warnings", [])

    for w in warnings:
        field_name = w.get("field", "")
        if field_name not in ranges:
            continue
        value = data.get(field_name)
        if value is None or not isinstance(value, (int, float)):
            continue
        min_v, max_v = ranges[field_name]
        if min_v <= value <= max_v:
            raise InvariantViolation(
                "false_plausibility_warning",
                f"False warning for {field_name}={value} within [{min_v}, {max_v}]",
                {"event_type": event_type, "field": field_name, "value": value, "warning": w},
            )


def assert_event_always_accepted(status_code: int) -> None:
    """Events are always accepted (even with warnings) — never 4xx for plausibility."""
    if status_code >= 400:
        raise InvariantViolation(
            "event_rejected_on_plausibility",
            f"Event was rejected (status {status_code}) but plausibility checks should be soft",
            {"status": status_code},
        )


def assert_certainty_contract_violation(
    response: dict[str, Any],
    status_code: int,
    expected_field: str,
    expected_variant: str,
) -> None:
    """Assert that a certainty contract violation is correctly reported."""
    code = assert_policy_violation(response, status_code)
    expected_prefix = "session_feedback_"
    if not code.startswith(expected_prefix):
        raise InvariantViolation(
            "certainty_wrong_code_prefix",
            f"Expected code starting with '{expected_prefix}', got '{code}'",
            {"code": code},
        )
