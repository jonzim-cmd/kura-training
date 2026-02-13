"""Layer 2: Adversarial Scenario Tests.

Runs built-in scenarios and (optionally) LLM-generated scenarios against
the real API. Tests both hand-crafted edge cases and dynamically generated
adversarial payloads.

Run:
    # Built-in scenarios only
    KURA_API_KEY=... uv run pytest tests/fuzzing/test_scenarios.py -v

    # Include LLM-generated scenarios (requires ANTHROPIC_API_KEY)
    KURA_API_KEY=... ANTHROPIC_API_KEY=... uv run pytest tests/fuzzing/test_scenarios.py -v --live-llm
"""

from __future__ import annotations

import pytest

from .conftest import KuraTestClient, pytestmark  # noqa: F401
from .scenarios import BUILTIN_SCENARIOS, AdversarialScenario
from .runner import run_scenario


# --- Builtin scenarios ---


@pytest.mark.parametrize(
    "scenario",
    BUILTIN_SCENARIOS,
    ids=[s.id for s in BUILTIN_SCENARIOS],
)
def test_builtin_scenario(api_client: KuraTestClient, scenario: AdversarialScenario):
    """Run a single built-in adversarial scenario."""
    result = run_scenario(api_client, scenario)
    assert result.passed, (
        f"Scenario '{scenario.id}' failed: {result.error_message}\n"
        f"  Expected: {scenario.expected_behavior}\n"
        f"  Got: status={result.actual_status}, code={result.actual_code}\n"
        f"  Warnings: {result.actual_warnings}"
    )


# --- LLM-generated scenarios ---


def pytest_addoption(parser):
    """Add --live-llm option for LLM scenario generation."""
    parser.addoption(
        "--live-llm",
        action="store_true",
        default=False,
        help="Generate scenarios using Claude API (requires ANTHROPIC_API_KEY)",
    )


@pytest.fixture
def llm_scenarios(request) -> list[AdversarialScenario]:
    """Load LLM-generated scenarios (cached or live)."""
    from .generator import generate_all

    live = request.config.getoption("--live-llm", default=False)
    return generate_all(count_per_category=5, live=live)


def test_llm_generated_scenarios(api_client: KuraTestClient, llm_scenarios):
    """Run all LLM-generated scenarios."""
    if not llm_scenarios:
        pytest.skip("No LLM scenarios available (run with --live-llm to generate)")

    results = []
    for scenario in llm_scenarios:
        result = run_scenario(api_client, scenario)
        results.append(result)

    failures = [r for r in results if not r.passed]
    if failures:
        failure_details = "\n".join(
            f"  [{r.scenario_id}] {r.error_message}" for r in failures
        )
        pytest.fail(
            f"{len(failures)}/{len(results)} LLM scenarios failed:\n{failure_details}"
        )


# --- Category-specific test classes ---


class TestLocaleScenarios:
    """Locale-specific adversarial scenarios."""

    @pytest.mark.parametrize(
        "scenario",
        [s for s in BUILTIN_SCENARIOS if s.category == "locale"],
        ids=[s.id for s in BUILTIN_SCENARIOS if s.category == "locale"],
    )
    def test_locale_scenario(self, api_client: KuraTestClient, scenario: AdversarialScenario):
        result = run_scenario(api_client, scenario)
        assert result.passed, f"{scenario.id}: {result.error_message}"


class TestBoundaryScenarios:
    """Boundary value adversarial scenarios."""

    @pytest.mark.parametrize(
        "scenario",
        [s for s in BUILTIN_SCENARIOS if s.category == "boundary"],
        ids=[s.id for s in BUILTIN_SCENARIOS if s.category == "boundary"],
    )
    def test_boundary_scenario(self, api_client: KuraTestClient, scenario: AdversarialScenario):
        result = run_scenario(api_client, scenario)
        assert result.passed, f"{scenario.id}: {result.error_message}"


class TestTypeConfusionScenarios:
    """Type confusion adversarial scenarios."""

    @pytest.mark.parametrize(
        "scenario",
        [s for s in BUILTIN_SCENARIOS if s.category == "type_confusion"],
        ids=[s.id for s in BUILTIN_SCENARIOS if s.category == "type_confusion"],
    )
    def test_type_confusion_scenario(self, api_client: KuraTestClient, scenario: AdversarialScenario):
        result = run_scenario(api_client, scenario)
        assert result.passed, f"{scenario.id}: {result.error_message}"


class TestCertaintyScenarios:
    """Certainty contract adversarial scenarios."""

    @pytest.mark.parametrize(
        "scenario",
        [s for s in BUILTIN_SCENARIOS if s.category == "certainty"],
        ids=[s.id for s in BUILTIN_SCENARIOS if s.category == "certainty"],
    )
    def test_certainty_scenario(self, api_client: KuraTestClient, scenario: AdversarialScenario):
        result = run_scenario(api_client, scenario)
        assert result.passed, f"{scenario.id}: {result.error_message}"
