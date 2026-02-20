"""Observational causal effect estimation utilities.

This module provides a lightweight causal layer:
- propensity score estimation (logistic regression via gradient descent),
- inverse-propensity weighting (IPW) for average treatment effect (ATE),
- bootstrap uncertainty intervals,
- machine-readable caveats for agent-facing transparency.
"""

from __future__ import annotations

import math
import random
from typing import Any

ASSUMPTIONS: list[dict[str, str]] = [
    {
        "code": "consistency",
        "description": "Each treatment flag represents one well-defined intervention variant.",
    },
    {
        "code": "no_unmeasured_confounding",
        "description": "Relevant confounders are observed and included in the propensity model.",
    },
    {
        "code": "positivity",
        "description": "Each observation has a non-zero chance for treatment and control.",
    },
    {
        "code": "no_interference",
        "description": "One day's intervention does not directly alter another day's outcome unit.",
    },
    {
        "code": "model_specification",
        "description": "The propensity model is flexible enough for treatment assignment patterns.",
    },
]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_binary(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    parsed = _as_float(value)
    if parsed is None:
        return None
    return 1 if parsed >= 0.5 else 0


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _variance(values: list[float], center: float | None = None) -> float:
    if len(values) <= 1:
        return 0.0
    mu = _mean(values) if center is None else center
    return sum((v - mu) ** 2 for v in values) / (len(values) - 1)


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    q = _clamp(q, 0.0, 1.0)
    pos = q * (len(ordered) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    mix = pos - lo
    return ((1.0 - mix) * ordered[lo]) + (mix * ordered[hi])


def _sigmoid(value: float) -> float:
    if value >= 0:
        exp_neg = math.exp(-value)
        return 1.0 / (1.0 + exp_neg)
    exp_pos = math.exp(value)
    return exp_pos / (1.0 + exp_pos)


def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    sigma = max(1e-9, sigma)
    z = (x - mu) / (sigma * math.sqrt(2.0))
    return 0.5 * (1.0 + math.erf(z))


def _standardize(matrix: list[list[float]]) -> tuple[list[list[float]], list[float], list[float]]:
    if not matrix:
        return [], [], []
    cols = len(matrix[0])
    means = [0.0] * cols
    stds = [1.0] * cols

    for col in range(cols):
        col_vals = [row[col] for row in matrix]
        mu = _mean(col_vals)
        var = _variance(col_vals, center=mu)
        sd = math.sqrt(max(var, 1e-12))
        means[col] = mu
        stds[col] = sd

    standardized: list[list[float]] = []
    for row in matrix:
        standardized.append([(row[idx] - means[idx]) / stds[idx] for idx in range(cols)])
    return standardized, means, stds


def _fit_logistic(
    features: list[list[float]],
    targets: list[int],
    *,
    learning_rate: float = 0.12,
    l2: float = 0.02,
    iterations: int = 700,
) -> tuple[float, list[float]]:
    if not features:
        return 0.0, []
    n = len(features)
    d = len(features[0])
    bias = 0.0
    weights = [0.0] * d

    for step in range(iterations):
        grad_b = 0.0
        grad_w = [0.0] * d
        for idx, row in enumerate(features):
            z = bias + sum(weights[j] * row[j] for j in range(d))
            prob = _sigmoid(z)
            err = prob - targets[idx]
            grad_b += err
            for j in range(d):
                grad_w[j] += err * row[j]

        inv_n = 1.0 / max(1, n)
        lr = learning_rate / (1.0 + 0.004 * step)
        bias -= lr * (grad_b * inv_n)
        for j in range(d):
            grad = (grad_w[j] * inv_n) + (l2 * weights[j])
            weights[j] -= lr * grad

    return bias, weights


def _fit_linear_regression(
    features: list[list[float]],
    targets: list[float],
    *,
    learning_rate: float = 0.08,
    l2: float = 0.03,
    iterations: int = 800,
) -> tuple[float, list[float]]:
    if not features:
        return 0.0, []
    n = len(features)
    d = len(features[0])
    bias = _mean(targets)
    weights = [0.0] * d

    for step in range(iterations):
        grad_b = 0.0
        grad_w = [0.0] * d
        for idx, row in enumerate(features):
            prediction = bias + sum(weights[j] * row[j] for j in range(d))
            err = prediction - targets[idx]
            grad_b += err
            for j in range(d):
                grad_w[j] += err * row[j]
        inv_n = 1.0 / max(1, n)
        lr = learning_rate / (1.0 + 0.003 * step)
        bias -= lr * (grad_b * inv_n)
        for j in range(d):
            grad = (grad_w[j] * inv_n) + (l2 * weights[j])
            weights[j] -= lr * grad
    return bias, weights


def _predict_linear(bias: float, weights: list[float], row: list[float]) -> float:
    return bias + sum(weights[idx] * row[idx] for idx in range(min(len(weights), len(row))))


def _standardize_with_reference(
    matrix: list[list[float]],
    means: list[float],
    stds: list[float],
) -> list[list[float]]:
    if not matrix:
        return []
    out: list[list[float]] = []
    for row in matrix:
        out.append(
            [
                (row[idx] - means[idx]) / max(stds[idx], 1e-12)
                for idx in range(min(len(row), len(means)))
            ]
        )
    return out


def _estimate_aipw_crossfit(
    rows: list[dict[str, Any]],
    feature_names: list[str],
    *,
    overlap_floor: float,
    folds: int = 3,
) -> dict[str, Any] | None:
    n = len(rows)
    if n < 12:
        return None
    folds = max(2, min(int(folds), n))

    feature_matrix = [
        [float(row["confounders"].get(name, 0.0)) for name in feature_names]
        for row in rows
    ]
    treated = [int(row["treated"]) for row in rows]
    outcomes = [float(row["outcome"]) for row in rows]

    propensity_hat: list[float | None] = [None] * n
    mu1_hat: list[float | None] = [None] * n
    mu0_hat: list[float | None] = [None] * n
    fold_sizes: list[int] = []

    index_to_fold = {idx: (idx % folds) for idx in range(n)}
    for fold_idx in range(folds):
        test_indices = [idx for idx in range(n) if index_to_fold[idx] == fold_idx]
        train_indices = [idx for idx in range(n) if index_to_fold[idx] != fold_idx]
        if not test_indices or not train_indices:
            return None

        x_train_raw = [feature_matrix[idx] for idx in train_indices]
        x_train, means, stds = _standardize(x_train_raw)
        y_treat_train = [treated[idx] for idx in train_indices]
        y_outcome_train = [outcomes[idx] for idx in train_indices]

        treated_train_count = sum(y_treat_train)
        control_train_count = len(y_treat_train) - treated_train_count
        if treated_train_count < 3 or control_train_count < 3:
            return None

        prop_bias, prop_weights = _fit_logistic(x_train, y_treat_train)

        treated_features = [x_train[i] for i, t in enumerate(y_treat_train) if t == 1]
        treated_outcomes = [y_outcome_train[i] for i, t in enumerate(y_treat_train) if t == 1]
        control_features = [x_train[i] for i, t in enumerate(y_treat_train) if t == 0]
        control_outcomes = [y_outcome_train[i] for i, t in enumerate(y_treat_train) if t == 0]
        if len(treated_features) < 3 or len(control_features) < 3:
            return None

        mu1_bias, mu1_weights = _fit_linear_regression(treated_features, treated_outcomes)
        mu0_bias, mu0_weights = _fit_linear_regression(control_features, control_outcomes)

        x_test_raw = [feature_matrix[idx] for idx in test_indices]
        x_test = _standardize_with_reference(x_test_raw, means, stds)
        fold_sizes.append(len(test_indices))
        for pos, row_idx in enumerate(test_indices):
            row = x_test[pos]
            e_hat = _sigmoid(prop_bias + sum(prop_weights[j] * row[j] for j in range(len(prop_weights))))
            e_hat = _clamp(e_hat, overlap_floor, 1.0 - overlap_floor)
            propensity_hat[row_idx] = e_hat
            mu1_hat[row_idx] = _predict_linear(mu1_bias, mu1_weights, row)
            mu0_hat[row_idx] = _predict_linear(mu0_bias, mu0_weights, row)

    if any(v is None for v in propensity_hat) or any(v is None for v in mu1_hat) or any(v is None for v in mu0_hat):
        return None

    e = [float(v) for v in propensity_hat]
    mu1 = [float(v) for v in mu1_hat]
    mu0 = [float(v) for v in mu0_hat]

    pseudo_outcomes: list[float] = []
    treated_props: list[float] = []
    control_props: list[float] = []
    aipw_weights: list[float] = []
    treated_weights: list[float] = []
    control_weights: list[float] = []
    for idx in range(n):
        t = treated[idx]
        y = outcomes[idx]
        e_i = _clamp(e[idx], overlap_floor, 1.0 - overlap_floor)
        m1_i = mu1[idx]
        m0_i = mu0[idx]
        pseudo = m1_i - m0_i + (t * (y - m1_i) / e_i) - ((1 - t) * (y - m0_i) / (1.0 - e_i))
        pseudo_outcomes.append(pseudo)

        if t == 1:
            treated_props.append(e_i)
            w = 1.0 / e_i
            treated_weights.append(w)
            control_weights.append(0.0)
            aipw_weights.append(w)
        else:
            control_props.append(e_i)
            w = 1.0 / (1.0 - e_i)
            treated_weights.append(0.0)
            control_weights.append(w)
            aipw_weights.append(w)

    ate = _mean(pseudo_outcomes)
    pseudo_var = _variance(pseudo_outcomes, center=ate)
    if not math.isfinite(pseudo_var):
        return None
    effect_sd = math.sqrt(max(pseudo_var, 1e-9))
    se = effect_sd / math.sqrt(max(1, n))
    delta = 1.96 * max(se, 1e-9)
    ci95 = [ate - delta, ate + delta]
    probability_positive = 1.0 - _normal_cdf(0.0, ate, max(se, 1e-9))

    treated_weight_sum = sum(treated_weights)
    control_weight_sum = sum(control_weights)
    treated_effective_n = (
        (treated_weight_sum ** 2)
        / max(sum(w * w for w in treated_weights if w > 0.0), 1e-9)
    )
    control_effective_n = (
        (control_weight_sum ** 2)
        / max(sum(w * w for w in control_weights if w > 0.0), 1e-9)
    )
    overlap_low = max(min(treated_props), min(control_props))
    overlap_high = min(max(treated_props), max(control_props))
    overlap_width = max(0.0, overlap_high - overlap_low)

    return {
        "ate": ate,
        "ci95": ci95,
        "effect_sd": effect_sd,
        "probability_positive": probability_positive,
        "folds": folds,
        "fold_sizes": fold_sizes,
        "diagnostics": {
            "overlap": {
                "treated_propensity_range": [min(treated_props), max(treated_props)],
                "control_propensity_range": [min(control_props), max(control_props)],
                "overlap_range": [overlap_low, overlap_high],
                "overlap_width": overlap_width,
            },
            "weights": {
                "max": max(aipw_weights),
                "p95": _quantile(aipw_weights, 0.95),
            },
            "effective_sample_size": {
                "treated": treated_effective_n,
                "control": control_effective_n,
            },
        },
    }


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    total_weight = sum(weights)
    if total_weight <= 0.0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights)) / total_weight


def _weighted_variance(values: list[float], weights: list[float], center: float) -> float:
    total_weight = sum(weights)
    if total_weight <= 0.0:
        return 0.0
    return sum(w * (v - center) ** 2 for v, w in zip(values, weights)) / total_weight


def _standardized_mean_difference(
    treated_values: list[float],
    control_values: list[float],
    treated_weights: list[float] | None = None,
    control_weights: list[float] | None = None,
) -> float:
    if not treated_values or not control_values:
        return 0.0

    if treated_weights is None:
        mu_t = _mean(treated_values)
        var_t = _variance(treated_values, center=mu_t)
    else:
        mu_t = _weighted_mean(treated_values, treated_weights)
        var_t = _weighted_variance(treated_values, treated_weights, mu_t)

    if control_weights is None:
        mu_c = _mean(control_values)
        var_c = _variance(control_values, center=mu_c)
    else:
        mu_c = _weighted_mean(control_values, control_weights)
        var_c = _weighted_variance(control_values, control_weights, mu_c)

    denom = math.sqrt(max((var_t + var_c) / 2.0, 1e-12))
    return (mu_t - mu_c) / denom


def _normalize_samples(
    samples: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    normalized: list[dict[str, Any]] = []
    feature_names: set[str] = set()

    for sample in samples:
        treated = _as_binary(sample.get("treated"))
        outcome = _as_float(sample.get("outcome"))
        if treated is None or outcome is None:
            continue

        confounders_raw = sample.get("confounders")
        confounders: dict[str, float] = {}
        if isinstance(confounders_raw, dict):
            for key, value in confounders_raw.items():
                parsed = _as_float(value)
                if parsed is None:
                    continue
                key_str = str(key)
                confounders[key_str] = parsed
                feature_names.add(key_str)

        normalized.append(
            {
                "treated": treated,
                "outcome": outcome,
                "confounders": confounders,
            }
        )

    return normalized, sorted(feature_names)


def _estimate_once(
    rows: list[dict[str, Any]],
    feature_names: list[str],
    *,
    overlap_floor: float,
) -> dict[str, Any] | None:
    if not rows:
        return None

    outcomes = [float(row["outcome"]) for row in rows]
    treated = [int(row["treated"]) for row in rows]
    treated_rate = _mean([float(v) for v in treated])

    if treated_rate <= 0.0 or treated_rate >= 1.0:
        return None

    feature_matrix = [
        [float(row["confounders"].get(name, 0.0)) for name in feature_names]
        for row in rows
    ]
    standardized_matrix, means, stds = _standardize(feature_matrix)

    if feature_names:
        bias, coefficients = _fit_logistic(standardized_matrix, treated)
        propensity_raw = [
            _sigmoid(bias + sum(coefficients[j] * row[j] for j in range(len(feature_names))))
            for row in standardized_matrix
        ]
        model = {
            "method": "logistic_gradient_descent",
            "feature_names": feature_names,
            "intercept": round(bias, 6),
            "coefficients": {name: round(coefficients[idx], 6) for idx, name in enumerate(feature_names)},
            "standardization": {
                name: {"mean": round(means[idx], 6), "std": round(stds[idx], 6)}
                for idx, name in enumerate(feature_names)
            },
        }
    else:
        propensity_raw = [treated_rate] * len(rows)
        model = {
            "method": "intercept_only",
            "feature_names": [],
            "intercept": round(math.log(treated_rate / (1.0 - treated_rate)), 6),
            "coefficients": {},
            "standardization": {},
        }

    propensities = [_clamp(p, overlap_floor, 1.0 - overlap_floor) for p in propensity_raw]

    treated_weights: list[float] = []
    control_weights: list[float] = []
    all_weights: list[float] = []
    for idx, is_treated in enumerate(treated):
        if is_treated == 1:
            w = treated_rate / max(propensities[idx], 1e-9)
            treated_weights.append(w)
            control_weights.append(0.0)
        else:
            w = (1.0 - treated_rate) / max(1.0 - propensities[idx], 1e-9)
            treated_weights.append(0.0)
            control_weights.append(w)
        all_weights.append(w)

    treated_weight_sum = sum(treated_weights)
    control_weight_sum = sum(control_weights)
    if treated_weight_sum <= 0.0 or control_weight_sum <= 0.0:
        return None

    treated_mean = sum(
        outcomes[idx] * treated_weights[idx] for idx in range(len(rows))
    ) / treated_weight_sum
    control_mean = sum(
        outcomes[idx] * control_weights[idx] for idx in range(len(rows))
    ) / control_weight_sum
    ate = treated_mean - control_mean

    treated_effective_n = (
        (treated_weight_sum ** 2)
        / max(sum(w * w for w in treated_weights if w > 0.0), 1e-9)
    )
    control_effective_n = (
        (control_weight_sum ** 2)
        / max(sum(w * w for w in control_weights if w > 0.0), 1e-9)
    )

    treated_props = [propensities[idx] for idx, flag in enumerate(treated) if flag == 1]
    control_props = [propensities[idx] for idx, flag in enumerate(treated) if flag == 0]
    overlap_low = max(min(treated_props), min(control_props))
    overlap_high = min(max(treated_props), max(control_props))
    overlap_width = max(0.0, overlap_high - overlap_low)

    before_balance: dict[str, float] = {}
    after_balance: dict[str, float] = {}
    if feature_names:
        for idx, name in enumerate(feature_names):
            t_vals = [standardized_matrix[row_idx][idx] for row_idx, flag in enumerate(treated) if flag == 1]
            c_vals = [standardized_matrix[row_idx][idx] for row_idx, flag in enumerate(treated) if flag == 0]
            t_w = [treated_weights[row_idx] for row_idx, flag in enumerate(treated) if flag == 1]
            c_w = [control_weights[row_idx] for row_idx, flag in enumerate(treated) if flag == 0]
            before_balance[name] = _standardized_mean_difference(t_vals, c_vals)
            after_balance[name] = _standardized_mean_difference(
                t_vals,
                c_vals,
                treated_weights=t_w,
                control_weights=c_w,
            )

    mean_abs_before = _mean([abs(value) for value in before_balance.values()]) if before_balance else 0.0
    mean_abs_after = _mean([abs(value) for value in after_balance.values()]) if after_balance else 0.0

    return {
        "ate": ate,
        "weights": all_weights,
        "propensities": propensities,
        "model": model,
        "diagnostics": {
            "treated_weighted_mean": treated_mean,
            "control_weighted_mean": control_mean,
            "effective_sample_size": {
                "treated": treated_effective_n,
                "control": control_effective_n,
            },
            "overlap": {
                "treated_propensity_range": [min(treated_props), max(treated_props)],
                "control_propensity_range": [min(control_props), max(control_props)],
                "overlap_range": [overlap_low, overlap_high],
                "overlap_width": overlap_width,
            },
            "weights": {
                "max": max(all_weights),
                "p95": _quantile(all_weights, 0.95),
            },
            "balance": {
                "before": {k: round(v, 4) for k, v in before_balance.items()},
                "after": {k: round(v, 4) for k, v in after_balance.items()},
                "mean_abs_smd_before": mean_abs_before,
                "mean_abs_smd_after": mean_abs_after,
            },
        },
    }


def _bootstrap_ates(
    rows: list[dict[str, Any]],
    feature_names: list[str],
    *,
    overlap_floor: float,
    bootstrap_samples: int,
) -> list[float]:
    rng = random.Random(42)
    estimates: list[float] = []
    n = len(rows)
    if n == 0:
        return estimates

    for _ in range(bootstrap_samples):
        sample = [rows[rng.randrange(n)] for _ in range(n)]
        estimate = _estimate_once(sample, feature_names, overlap_floor=overlap_floor)
        if estimate is None:
            continue
        estimates.append(float(estimate["ate"]))
    return estimates


def estimate_intervention_effect(
    samples: list[dict[str, Any]],
    *,
    bootstrap_samples: int = 250,
    min_samples: int = 24,
    overlap_floor: float = 0.03,
) -> dict[str, Any]:
    """Estimate observational intervention effect with propensity adjustment."""
    rows, feature_names = _normalize_samples(samples)
    total = len(rows)
    treated_count = sum(int(row["treated"]) for row in rows)
    control_count = total - treated_count
    min_group_size = max(4, min_samples // 6)

    caveats: list[dict[str, Any]] = []
    if total < min_samples or treated_count < min_group_size or control_count < min_group_size:
        caveats.append(
            {
                "code": "insufficient_samples",
                "severity": "high",
                "details": {
                    "required_samples": min_samples,
                    "required_group_size": min_group_size,
                    "observed_samples": total,
                    "treated_samples": treated_count,
                    "control_samples": control_count,
                },
            }
        )
        return {
            "status": "insufficient_data",
            "estimand": "average_treatment_effect",
            "assumptions": ASSUMPTIONS,
            "effect": None,
            "propensity": {
                "method": "logistic_ipw",
                "feature_names": feature_names,
                "treated_prevalence": round((_mean([float(r["treated"]) for r in rows]) if rows else 0.0), 4),
            },
            "diagnostics": {
                "observed_samples": total,
                "treated_samples": treated_count,
                "control_samples": control_count,
            },
            "caveats": caveats,
        }

    estimate = _estimate_once(rows, feature_names, overlap_floor=overlap_floor)
    if estimate is None:
        caveats.append(
            {
                "code": "positivity_violation",
                "severity": "high",
                "details": {
                    "observed_samples": total,
                    "treated_samples": treated_count,
                    "control_samples": control_count,
                },
            }
        )
        return {
            "status": "insufficient_data",
            "estimand": "average_treatment_effect",
            "assumptions": ASSUMPTIONS,
            "effect": None,
            "propensity": {
                "method": "logistic_ipw",
                "feature_names": feature_names,
                "treated_prevalence": round(_mean([float(r["treated"]) for r in rows]), 4),
            },
            "diagnostics": {
                "observed_samples": total,
                "treated_samples": treated_count,
                "control_samples": control_count,
            },
            "caveats": caveats,
        }

    bootstrap_ates = _bootstrap_ates(
        rows,
        feature_names,
        overlap_floor=overlap_floor,
        bootstrap_samples=max(20, bootstrap_samples),
    )

    diagnostics = dict(estimate["diagnostics"])
    diagnostics["observed_samples"] = total
    diagnostics["treated_samples"] = treated_count
    diagnostics["control_samples"] = control_count
    diagnostics["outcome_std"] = math.sqrt(max(_variance([float(row["outcome"]) for row in rows]), 0.0))
    diagnostics["bootstrap_valid_samples"] = len(bootstrap_ates)

    aipw = _estimate_aipw_crossfit(
        rows,
        feature_names,
        overlap_floor=overlap_floor,
        folds=3,
    )
    estimator_method = "aipw_crossfit"
    fallback_reason: str | None = None
    if aipw is not None:
        mean_ate = float(aipw["ate"])
        ci95 = [float(aipw["ci95"][0]), float(aipw["ci95"][1])]
        effect_sd = float(aipw["effect_sd"])
        probability_positive = float(aipw["probability_positive"])
        aipw_diag = dict(aipw.get("diagnostics") or {})
        if isinstance(aipw_diag.get("overlap"), dict):
            diagnostics["overlap"] = aipw_diag["overlap"]
        if isinstance(aipw_diag.get("weights"), dict):
            diagnostics["weights"] = aipw_diag["weights"]
        if isinstance(aipw_diag.get("effective_sample_size"), dict):
            diagnostics["effective_sample_size"] = aipw_diag["effective_sample_size"]
        diagnostics["effect_sd"] = effect_sd
        diagnostics["estimator"] = {
            "method": "aipw_crossfit",
            "folds": int(aipw.get("folds", 0) or 0),
            "fold_sizes": list(aipw.get("fold_sizes") or []),
        }
    else:
        estimator_method = "logistic_ipw_fallback"
        fallback_reason = "aipw_crossfit_unavailable"
        mean_ate = float(estimate["ate"])

        if len(bootstrap_ates) >= 25:
            ci95 = [_quantile(bootstrap_ates, 0.025), _quantile(bootstrap_ates, 0.975)]
            effect_sd = math.sqrt(max(_variance(bootstrap_ates), 0.0))
            probability_positive = sum(1 for value in bootstrap_ates if value > 0.0) / len(bootstrap_ates)
        else:
            effect_sd = math.sqrt(max(_variance([mean_ate]), 0.0))
            delta = 1.96 * max(0.01, effect_sd)
            ci95 = [mean_ate - delta, mean_ate + delta]
            probability_positive = 1.0 if mean_ate > 0.0 else 0.0
        diagnostics["effect_sd"] = effect_sd
        diagnostics["estimator"] = {
            "method": "logistic_ipw_fallback",
            "fallback_reason": fallback_reason,
        }

    overlap_width = float(diagnostics["overlap"]["overlap_width"])
    weight_max = float(diagnostics["weights"]["max"])
    weight_p95 = float(diagnostics["weights"]["p95"])
    eff_t = float(diagnostics["effective_sample_size"]["treated"])
    eff_c = float(diagnostics["effective_sample_size"]["control"])
    mean_abs_smd_after = float((diagnostics.get("balance") or {}).get("mean_abs_smd_after", 0.0) or 0.0)

    if overlap_width < 0.12:
        caveats.append(
            {
                "code": "weak_overlap",
                "severity": "medium",
                "details": {"overlap_width": round(overlap_width, 4)},
            }
        )
    if weight_max > 12.0 or weight_p95 > 6.0:
        caveats.append(
            {
                "code": "extreme_weights",
                "severity": "medium",
                "details": {"max_weight": round(weight_max, 4), "p95_weight": round(weight_p95, 4)},
            }
        )
    if eff_t < 8.0 or eff_c < 8.0:
        caveats.append(
            {
                "code": "low_effective_sample_size",
                "severity": "medium",
                "details": {"treated_effective_n": round(eff_t, 2), "control_effective_n": round(eff_c, 2)},
            }
        )
    if mean_abs_smd_after > 0.2:
        caveats.append(
            {
                "code": "residual_confounding_risk",
                "severity": "medium",
                "details": {"mean_abs_smd_after": round(mean_abs_smd_after, 4)},
            }
        )
    if diagnostics["outcome_std"] < 0.03:
        caveats.append(
            {
                "code": "low_outcome_variance",
                "severity": "low",
                "details": {"outcome_std": round(float(diagnostics["outcome_std"]), 4)},
            }
        )
    if (ci95[1] - ci95[0]) > 0.25:
        caveats.append(
            {
                "code": "wide_interval",
                "severity": "low",
                "details": {"ci95_width": round(ci95[1] - ci95[0], 4)},
            }
        )

    direction = "uncertain"
    if ci95[0] > 0.0:
        direction = "positive"
    elif ci95[1] < 0.0:
        direction = "negative"

    return {
        "status": "ok",
        "estimand": "average_treatment_effect",
        "assumptions": ASSUMPTIONS,
        "effect": {
            "mean_ate": round(mean_ate, 4),
            "ci95": [round(ci95[0], 4), round(ci95[1], 4)],
            "direction": direction,
            "probability_positive": round(float(probability_positive), 4),
        },
        "propensity": {
            "method": estimator_method,
            "legacy_method": "logistic_ipw",
            "treated_prevalence": round(_mean([float(row["treated"]) for row in rows]), 4),
            "feature_names": feature_names,
            "model": estimate["model"],
        },
        "diagnostics": {
            "observed_samples": diagnostics["observed_samples"],
            "treated_samples": diagnostics["treated_samples"],
            "control_samples": diagnostics["control_samples"],
            "outcome_std": round(float(diagnostics["outcome_std"]), 4),
            "bootstrap_valid_samples": diagnostics["bootstrap_valid_samples"],
            "effect_sd": round(float(diagnostics["effect_sd"]), 4),
            "estimator": diagnostics.get("estimator"),
            "overlap": {
                "treated_propensity_range": [
                    round(float(diagnostics["overlap"]["treated_propensity_range"][0]), 4),
                    round(float(diagnostics["overlap"]["treated_propensity_range"][1]), 4),
                ],
                "control_propensity_range": [
                    round(float(diagnostics["overlap"]["control_propensity_range"][0]), 4),
                    round(float(diagnostics["overlap"]["control_propensity_range"][1]), 4),
                ],
                "overlap_range": [
                    round(float(diagnostics["overlap"]["overlap_range"][0]), 4),
                    round(float(diagnostics["overlap"]["overlap_range"][1]), 4),
                ],
                "overlap_width": round(float(diagnostics["overlap"]["overlap_width"]), 4),
            },
            "weights": {
                "max": round(float(diagnostics["weights"]["max"]), 4),
                "p95": round(float(diagnostics["weights"]["p95"]), 4),
            },
            "effective_sample_size": {
                "treated": round(float(diagnostics["effective_sample_size"]["treated"]), 2),
                "control": round(float(diagnostics["effective_sample_size"]["control"]), 2),
            },
            "balance": diagnostics["balance"],
            "fallback_reason": fallback_reason,
        },
        "caveats": caveats,
    }
