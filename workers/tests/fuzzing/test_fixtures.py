"""Layer 3: Transcript Regression Tests.

Runs committed fixture scenarios against the real API.
These are permanent regression tests — they should NEVER be removed
unless the underlying invariant intentionally changes.

Run:
    KURA_API_KEY=... uv run pytest tests/fuzzing/test_fixtures.py -v
"""

from __future__ import annotations

import pytest

from .conftest import KuraTestClient, pytestmark  # noqa: F401
from .runner import run_scenario
from .transcript import load_fixtures


# Load fixtures at module level for parametrize
_FIXTURES = load_fixtures()


@pytest.mark.parametrize(
    "scenario",
    _FIXTURES,
    ids=[s.id for s in _FIXTURES],
)
def test_fixture_scenario(api_client: KuraTestClient, scenario):
    """Run a committed fixture scenario against the API."""
    result = run_scenario(api_client, scenario)
    assert result.passed, (
        f"Fixture '{scenario.id}' failed: {result.error_message}\n"
        f"  Category: {scenario.category}\n"
        f"  Description: {scenario.description}\n"
        f"  Expected: {scenario.expected_behavior}\n"
        f"  Got: status={result.actual_status}, code={result.actual_code}\n"
        f"  Warnings: {result.actual_warnings}\n"
        f"  Tags: {scenario.tags}"
    )


def test_fixture_count():
    """Ensure we have a minimum number of fixtures (canary test)."""
    assert len(_FIXTURES) >= 5, (
        f"Expected at least 5 fixture scenarios, got {len(_FIXTURES)}. "
        f"Run 'python -m tests.fuzzing.generator --live' to generate more, "
        f"then promote interesting ones with promote_to_fixture()."
    )
