from __future__ import annotations

from pathlib import Path

from tests.architecture.conftest import assert_kura_api_test_passes


REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PAGE = REPO_ROOT / "web" / "src" / "app" / "[locale]" / "(inner)" / "settings" / "page.tsx"

RUNTIME_TESTS: tuple[str, ...] = (
    "routes::events::tests::health_consent_forbidden_error_contract_is_machine_readable",
    "routes::agent::tests::health_consent_write_gate_contract_schema_version_is_pinned",
    "routes::agent::tests::health_consent_write_gate_is_blocked_without_consent_and_has_remediation",
    "routes::agent::tests::health_consent_write_gate_allows_writes_when_consent_is_present",
)


def test_consent_interlock_runtime_contracts_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)


def test_settings_consent_disable_flow_keeps_explicit_confirmation_interlock() -> None:
    src = SETTINGS_PAGE.read_text(encoding="utf-8")

    assert "showHealthConsentDisableConfirm" in src
    assert "healthConsentDisableTitle" in src
    assert "healthConsentDisableConfirmButton" in src
    assert "handleConfirmDisableHealthConsent" in src
