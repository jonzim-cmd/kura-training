"""Operational model contract: agent_brief exposes Event Sourcing paradigm.

Architecture Decision (kura-training-nco):

Agents apply wrong mental models (REST-CRUD) to an Event Sourcing system.
The agent_brief must proactively establish the correct paradigm before the
agent's first action.  The operational_model block is DERIVED from existing
event_conventions (category + usage fields), not manually maintained.

Invariants enforced:
1. Every event_convention declares a category (structural metadata).
2. build_system_config() output contains an operational_model section.
3. operational_model is derived from event_conventions, not hardcoded.
4. AgentBrief struct on the Rust side includes operational_model.
"""
from __future__ import annotations

from pathlib import Path

from tests.architecture.conftest import assert_kura_api_test_passes

AGENT_ROUTE = Path("api/src/routes/agent.rs")

ALLOWED_CATEGORIES = frozenset({
    "tracking",
    "correction",
    "planning",
    "meta",
    "identity",
    "system",
})

REQUIRED_OPERATIONAL_MODEL_KEYS = frozenset({
    "paradigm",
    "mutations",
    "corrections",
    "state_access",
    "event_types",
    "common_operations",
})


# ---------------------------------------------------------------------------
# Python-side invariants (event_conventions + system_config)
# ---------------------------------------------------------------------------


def test_every_event_convention_has_category() -> None:
    """INV: Each event_convention entry MUST declare a 'category' field."""
    from kura_workers.event_conventions import get_event_conventions

    conventions = get_event_conventions()
    missing = []
    invalid = []
    for name, conv in conventions.items():
        if "category" not in conv:
            missing.append(name)
        elif conv["category"] not in ALLOWED_CATEGORIES:
            invalid.append((name, conv["category"]))

    assert not missing, (
        f"Event conventions without 'category' field: {missing}. "
        f"Allowed categories: {sorted(ALLOWED_CATEGORIES)}"
    )
    assert not invalid, (
        f"Event conventions with invalid category: {invalid}. "
        f"Allowed categories: {sorted(ALLOWED_CATEGORIES)}"
    )


def test_build_system_config_contains_operational_model() -> None:
    """INV: build_system_config() output MUST contain 'operational_model'."""
    from kura_workers.system_config import build_system_config

    config = build_system_config()
    assert "operational_model" in config, (
        "build_system_config() must include 'operational_model' key. "
        "This block is derived from event_conventions categories + usage."
    )


def test_operational_model_has_required_keys() -> None:
    """INV: operational_model must contain paradigm, mutations, corrections,
    state_access, event_types, and common_operations."""
    from kura_workers.system_config import build_system_config

    config = build_system_config()
    model = config.get("operational_model", {})
    missing = REQUIRED_OPERATIONAL_MODEL_KEYS - set(model.keys())
    assert not missing, (
        f"operational_model missing required keys: {sorted(missing)}. "
        f"Required: {sorted(REQUIRED_OPERATIONAL_MODEL_KEYS)}"
    )


def test_operational_model_corrections_derived_from_conventions() -> None:
    """INV: operational_model.corrections must reference event.retracted,
    ensuring it is derived from event_conventions, not hardcoded."""
    from kura_workers.system_config import build_system_config

    config = build_system_config()
    model = config.get("operational_model", {})
    corrections = model.get("corrections", "")
    assert "event.retracted" in corrections, (
        "operational_model.corrections must reference 'event.retracted' — "
        "it should be derived from the correction-category event conventions."
    )


def test_operational_model_common_operations_is_list() -> None:
    """INV: common_operations must be a non-empty list of dicts with
    pattern, via, and hint keys."""
    from kura_workers.system_config import build_system_config

    config = build_system_config()
    ops = config.get("operational_model", {}).get("common_operations", [])
    assert isinstance(ops, list), "common_operations must be a list"
    assert len(ops) > 0, "common_operations must not be empty"
    for i, op in enumerate(ops):
        assert isinstance(op, dict), f"common_operations[{i}] must be a dict"
        for key in ("pattern", "via", "hint"):
            assert key in op, (
                f"common_operations[{i}] missing '{key}'. "
                f"Required keys: pattern, via, hint"
            )


# ---------------------------------------------------------------------------
# Rust-side invariants (AgentBrief struct)
# ---------------------------------------------------------------------------


def test_agent_brief_declares_operational_model_field() -> None:
    """INV: AgentBrief struct must include an operational_model field."""
    src = AGENT_ROUTE.read_text(encoding="utf-8")
    assert "pub struct AgentOperationalModel" in src, (
        "api/src/routes/agent.rs must declare AgentOperationalModel struct"
    )
    assert "operational_model" in src, (
        "AgentBrief must include operational_model field"
    )


RUST_TESTS: tuple[str, ...] = (
    "routes::agent::tests::agent_brief_includes_operational_model_from_system_config",
)


def test_agent_brief_operational_model_runtime_contract() -> None:
    """Rust unit test: agent_brief populates operational_model from system_config."""
    import subprocess
    from tests.architecture.conftest import REPO_ROOT

    for test_name in RUST_TESTS:
        result = subprocess.run(
            ["cargo", "test", "-p", "kura-api", test_name, "--", "--exact"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"Rust test failed: {test_name}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
        # Ensure the test actually ran (not 0 matched)
        assert "1 passed" in result.stdout or "1 passed" in result.stderr, (
            f"Rust test '{test_name}' did not run (0 tests matched). "
            f"Create this test in api/src/routes/agent.rs.\n"
            f"STDOUT:\n{result.stdout}"
        )
