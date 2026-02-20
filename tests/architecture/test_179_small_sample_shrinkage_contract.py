from __future__ import annotations

from kura_workers.objective_statistical_contract_v1 import (
    objective_statistical_method_contract_v1,
)


def test_small_sample_policy_requires_shrinkage_and_caveat() -> None:
    contract = objective_statistical_method_contract_v1()
    policy = contract["sample_size_policy"]
    assert policy["small_sample_strategy"] == "hierarchical_shrinkage"
    assert policy["must_emit_small_n_caveat"] is True
    assert policy["min_samples_per_stratum"] >= 10
    assert policy["min_unique_users_per_stratum"] >= 5


def test_small_sample_policy_fallback_order_is_stable() -> None:
    contract = objective_statistical_method_contract_v1()
    policy = contract["sample_size_policy"]
    assert policy["fallback_order"] == ["stratum", "cohort", "global"]

