from __future__ import annotations

from kura_workers.issue_clustering import issue_cluster_settings
from kura_workers.system_config import _get_conventions
from kura_workers.unknown_dimension_mining import unknown_dimension_mining_settings
from tests.architecture.conftest import assert_kura_api_test_passes


RUNTIME_TESTS: tuple[str, ...] = (
    "routes::auth::tests::invite_email_binding_accepts_matching_email",
    "routes::auth::tests::invite_email_binding_rejects_mismatch",
    "middleware::adaptive_abuse::tests::denied_ratio_signal_is_tempered_for_small_samples",
    "middleware::adaptive_abuse::tests::denied_ratio_high_volume_triggers_high_confidence_signal",
)


def test_security_hardening_runtime_contracts_pass() -> None:
    for test_name in RUNTIME_TESTS:
        assert_kura_api_test_passes(test_name)


def test_cross_user_guard_defaults_are_declared_in_runtime_settings() -> None:
    clustering_settings = issue_cluster_settings()
    unknown_dimension_settings = unknown_dimension_mining_settings()

    assert clustering_settings.max_events_per_user_per_bucket >= 1
    assert unknown_dimension_settings.max_events_per_user_per_cluster >= 1


def test_cross_user_guard_defaults_are_declared_in_system_conventions() -> None:
    conventions = _get_conventions()

    learning_controls = conventions["learning_clustering_v1"]["false_positive_controls"]
    assert learning_controls["max_events_per_user_per_bucket_default"] >= 1

    unknown_defaults = conventions["unknown_dimension_mining_v1"]["defaults"]
    assert unknown_defaults["max_events_per_user_per_cluster"] >= 1
