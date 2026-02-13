"""LLM-powered adversarial scenario generator.

Uses Claude to generate creative edge cases that are hard to reach
with property-based testing alone. Supports caching to avoid
repeated API calls during test runs.

Usage:
    # Generate scenarios (cached by default)
    python -m tests.fuzzing.generator --output generated/

    # Force regeneration
    python -m tests.fuzzing.generator --output generated/ --live
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from .scenarios import AdversarialScenario

CACHE_DIR = Path(__file__).parent / "generated"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

GENERATION_PROMPT = """You are a security-minded QA engineer testing the Kura Training API.
Your job: generate adversarial event payloads that might break validation.

The API accepts training events via POST /v1/events with this structure:
{
    "event_type": "set.logged",
    "data": { ... event-specific fields ... },
    "metadata": { "idempotency_key": "unique-uuid" }
}

Key validation rules:
- RPE: 1-10 (accepts locale decimals like "8,5")
- RIR: 0-10 (accepts locale decimals)
- event.retracted needs valid UUID in retracted_event_id
- set.corrected needs target_event_id (UUID) + non-empty changed_fields object
- projection_rule.created needs name, rule_type (field_tracking|categorized_tracking), source_events, fields
- session.completed has certainty contract: confirmed needs value, inferred needs value+evidence, unresolved needs reason but NO value

For category: {category}

Generate {count} adversarial scenarios as a JSON array. Each scenario:
{{
    "id": "unique_id",
    "category": "{category}",
    "description": "what this tests",
    "events": [{{ "event_type": "...", "data": {{ ... }} }}],
    "expected_behavior": "accepted" | "rejected" | "warning",
    "expected_codes": ["inv_code"] (if rejected),
    "tags": ["tag1", "tag2"]
}}

Focus on edge cases that automated generation would miss:
- Unicode/encoding tricks
- Semantic contradictions
- Real-world LLM output mistakes (hallucinated fields, wrong types)
- Cultural/locale variations
- JSON structure manipulation

Return ONLY the JSON array, no explanation."""

CATEGORIES = [
    "locale",
    "encoding",
    "boundary",
    "type_confusion",
    "certainty",
    "retraction",
    "correction",
    "projection_rule",
    "batch",
    "semantic_contradiction",
    "llm_hallucination",
]


def _cache_key(category: str, count: int) -> str:
    """Generate a stable cache key for a category+count."""
    return hashlib.sha256(f"{category}:{count}".encode()).hexdigest()[:16]


def _load_cached(category: str, count: int) -> list[dict[str, Any]] | None:
    """Load cached scenarios if available and fresh (< 24h)."""
    cache_file = CACHE_DIR / f"{_cache_key(category, count)}.json"
    if not cache_file.exists():
        return None
    age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
    if age_hours > 24:
        return None
    try:
        return json.loads(cache_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(category: str, count: int, scenarios: list[dict[str, Any]]) -> None:
    """Save generated scenarios to cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{_cache_key(category, count)}.json"
    cache_file.write_text(json.dumps(scenarios, indent=2))


def generate_scenarios(
    category: str,
    count: int = 10,
    *,
    live: bool = False,
) -> list[AdversarialScenario]:
    """Generate adversarial scenarios for a category.

    Args:
        category: Scenario category (from CATEGORIES)
        count: Number of scenarios to generate
        live: If True, call Claude API. If False, use cache only.

    Returns:
        List of AdversarialScenario objects
    """
    if not live:
        cached = _load_cached(category, count)
        if cached:
            return [_dict_to_scenario(s) for s in cached]
        return []  # No cache, no live generation

    if not ANTHROPIC_API_KEY:
        return []

    try:
        import anthropic
    except ImportError:
        return []

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = GENERATION_PROMPT.format(category=category, count=count)

    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text
    # Extract JSON from response (may have markdown fences)
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]

    try:
        raw_scenarios = json.loads(text)
    except json.JSONDecodeError:
        return []

    _save_cache(category, count, raw_scenarios)
    return [_dict_to_scenario(s) for s in raw_scenarios]


def _dict_to_scenario(d: dict[str, Any]) -> AdversarialScenario:
    """Convert a dict to an AdversarialScenario."""
    return AdversarialScenario(
        id=d.get("id", "unknown"),
        category=d.get("category", "unknown"),
        description=d.get("description", ""),
        events=d.get("events", []),
        expected_behavior=d.get("expected_behavior", "accepted"),
        expected_codes=d.get("expected_codes", []),
        tags=d.get("tags", []),
        seed=d.get("seed"),
    )


def generate_all(count_per_category: int = 5, *, live: bool = False) -> list[AdversarialScenario]:
    """Generate scenarios for all categories."""
    all_scenarios: list[AdversarialScenario] = []
    for category in CATEGORIES:
        all_scenarios.extend(generate_scenarios(category, count_per_category, live=live))
    return all_scenarios


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate adversarial fuzzing scenarios")
    parser.add_argument("--output", default="generated/", help="Output directory")
    parser.add_argument("--live", action="store_true", help="Call Claude API (default: cache only)")
    parser.add_argument("--count", type=int, default=10, help="Scenarios per category")
    parser.add_argument("--category", help="Single category to generate")
    args = parser.parse_args()

    if args.category:
        scenarios = generate_scenarios(args.category, args.count, live=args.live)
    else:
        scenarios = generate_all(args.count, live=args.live)

    print(f"Generated {len(scenarios)} scenarios")
    for s in scenarios:
        print(f"  [{s.category}] {s.id}: {s.description}")
