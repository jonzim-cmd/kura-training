from __future__ import annotations

from pathlib import Path

from kura_workers.system_config import _get_agent_behavior


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_architecture_spec_folder_is_present_and_non_empty() -> None:
    spec_files = sorted(p for p in (REPO_ROOT / "tests" / "architecture").glob("test_*.py"))
    assert spec_files, "tests/architecture must contain executable spec tests"


def test_challenge_mode_contract_is_exposed_in_system_behavior() -> None:
    behavior = _get_agent_behavior()
    challenge_mode = behavior["operational"]["challenge_mode"]
    assert challenge_mode["schema_version"] == "challenge_mode.v1"
    assert challenge_mode["default"] == "auto"
    assert challenge_mode["discoverability"]["chat_only_control"] is True
