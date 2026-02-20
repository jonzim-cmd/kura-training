"""Executable specification for the staged gold-standard strength model."""

from __future__ import annotations

from typing import Any


def strength_gold_model_contract_v1() -> dict[str, Any]:
    return {
        "schema_version": "strength_gold_model.v1",
        "target_projection": "strength_inference",
        "model_family": "hierarchical_bayesian_state_space",
        "stages": [
            {
                "stage": "surrogate_runtime",
                "engine": "hierarchical_bayes_surrogate",
                "purpose": "online-compatible shrinkage baseline while full MCMC path is staged",
            },
            {
                "stage": "full_offline_refit",
                "engine": "hierarchical_bayes_mcmc",
                "purpose": "exercise-specific latent strength + protocol/error hierarchy",
            },
        ],
        "required_covariates": [
            "rir_or_rpe_context",
            "load_context.comparability_group",
            "time_offset_days",
            "readiness_signal_context",
        ],
        "required_diagnostics": [
            "estimator.method",
            "estimator.surrogate",
            "posterior_or_surrogate_uncertainty",
            "calibration_report_reference",
            "fallback_reason",
        ],
        "fallback_policy": {
            "fallback_engine": "closed_form",
            "must_surface_fallback_reason": True,
            "must_keep_schema_compatible": True,
        },
    }
