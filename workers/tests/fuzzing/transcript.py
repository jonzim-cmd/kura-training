"""Transcript replay and fixture promotion pipeline.

Layer 3: Regression fixtures from generated scenarios and real failures.
Fixtures in fixtures/ are committed; generated/ is gitignored.

Promotion flow:
1. LLM generator creates scenarios in generated/
2. Runner finds failures
3. promote_to_fixture() copies failing scenario to fixtures/
4. Committed fixtures run as deterministic regression tests
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .scenarios import AdversarialScenario

FIXTURES_DIR = Path(__file__).parent / "fixtures"
GENERATED_DIR = Path(__file__).parent / "generated"


def load_fixtures() -> list[AdversarialScenario]:
    """Load all committed fixture scenarios."""
    scenarios: list[AdversarialScenario] = []
    for fixture_file in sorted(FIXTURES_DIR.glob("*.json")):
        try:
            raw = json.loads(fixture_file.read_text())
            if isinstance(raw, list):
                for item in raw:
                    scenarios.append(_dict_to_scenario(item, source=fixture_file.name))
            elif isinstance(raw, dict):
                scenarios.append(_dict_to_scenario(raw, source=fixture_file.name))
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Warning: Failed to load fixture {fixture_file}: {e}")
    return scenarios


def load_generated() -> list[AdversarialScenario]:
    """Load all generated (non-committed) scenarios."""
    scenarios: list[AdversarialScenario] = []
    if not GENERATED_DIR.exists():
        return scenarios
    for gen_file in sorted(GENERATED_DIR.glob("*.json")):
        try:
            raw = json.loads(gen_file.read_text())
            if isinstance(raw, list):
                for item in raw:
                    scenarios.append(_dict_to_scenario(item, source=gen_file.name))
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Warning: Failed to load generated {gen_file}: {e}")
    return scenarios


def promote_to_fixture(
    scenario: AdversarialScenario,
    fixture_file: str | None = None,
) -> Path:
    """Promote a scenario to a committed fixture.

    Args:
        scenario: The scenario to promote
        fixture_file: Target fixture filename (default: {category}_{id}.json)

    Returns:
        Path to the created fixture file
    """
    if fixture_file is None:
        fixture_file = f"{scenario.category}_{scenario.id}.json"

    target = FIXTURES_DIR / fixture_file
    scenario_dict = _scenario_to_dict(scenario)

    # If target exists and is an array, append
    if target.exists():
        existing = json.loads(target.read_text())
        if isinstance(existing, list):
            existing.append(scenario_dict)
            target.write_text(json.dumps(existing, indent=2))
        else:
            # Convert to array
            target.write_text(json.dumps([existing, scenario_dict], indent=2))
    else:
        target.write_text(json.dumps([scenario_dict], indent=2))

    return target


def export_failures(
    results: list[dict[str, Any]],
    output_dir: Path | None = None,
) -> Path | None:
    """Export failing scenario results for analysis.

    Args:
        results: List of ScenarioResult.to_dict() results
        output_dir: Where to save (default: generated/)

    Returns:
        Path to the failures file, or None if no failures
    """
    failures = [r for r in results if not r.get("passed", True)]
    if not failures:
        return None

    if output_dir is None:
        output_dir = GENERATED_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    import time
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"failures_{timestamp}.json"
    output_file.write_text(json.dumps(failures, indent=2))
    return output_file


def _dict_to_scenario(d: dict[str, Any], source: str = "") -> AdversarialScenario:
    """Convert a dict to an AdversarialScenario."""
    return AdversarialScenario(
        id=d.get("id", "unknown"),
        category=d.get("category", "unknown"),
        description=d.get("description", ""),
        events=d.get("events", []),
        expected_behavior=d.get("expected_behavior", "accepted"),
        expected_codes=d.get("expected_codes", []),
        tags=d.get("tags", []) + ([f"source:{source}"] if source else []),
        seed=d.get("seed"),
    )


def _scenario_to_dict(scenario: AdversarialScenario) -> dict[str, Any]:
    """Convert a scenario to a serializable dict."""
    return {
        "id": scenario.id,
        "category": scenario.category,
        "description": scenario.description,
        "events": scenario.events,
        "expected_behavior": scenario.expected_behavior,
        "expected_codes": scenario.expected_codes,
        "tags": [t for t in scenario.tags if not t.startswith("source:")],
    }
