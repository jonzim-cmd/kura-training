"""Bayesian inference utilities for strength/readiness projections."""

from __future__ import annotations

import logging
import math
import os
from datetime import date, datetime
from statistics import mean
from typing import Any

logger = logging.getLogger(__name__)

_WEEKLY_PHASE_BY_WEEKDAY = (
    "week_start",
    "load_build",
    "load_build",
    "peak_load",
    "transition",
    "recovery",
    "recovery",
)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_or_none(value: float | None, digits: int) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _tail_weighted_average(values: list[float], window: int) -> float | None:
    if not values:
        return None
    tail = values[-max(1, window):]
    weights = [float(idx + 1) for idx in range(len(tail))]
    weighted_sum = sum(v * w for v, w in zip(tail, weights))
    weight_total = sum(weights)
    if weight_total <= 0.0:
        return None
    return weighted_sum / weight_total


def _derivative_samples(points: list[tuple[float, float]]) -> tuple[list[float], list[float]]:
    slopes: list[float] = []
    slope_midpoints: list[float] = []

    for idx in range(1, len(points)):
        x0, y0 = points[idx - 1]
        x1, y1 = points[idx]
        dx = x1 - x0
        if dx <= 0:
            continue
        slopes.append((y1 - y0) / dx)
        slope_midpoints.append((x0 + x1) / 2.0)

    accelerations: list[float] = []
    for idx in range(1, len(slopes)):
        dx = slope_midpoints[idx] - slope_midpoints[idx - 1]
        if dx <= 0:
            continue
        accelerations.append((slopes[idx] - slopes[idx - 1]) / dx)

    return slopes, accelerations


def _trajectory_code(
    velocity: float | None,
    acceleration: float | None,
    *,
    velocity_epsilon: float,
    acceleration_epsilon: float,
) -> tuple[str, str, str, str]:
    if velocity is None:
        return "unknown", "unknown", "unknown", "unknown"

    direction = "flat"
    if velocity > velocity_epsilon:
        direction = "up"
    elif velocity < -velocity_epsilon:
        direction = "down"

    momentum = "steady"
    if acceleration is not None:
        if direction == "up":
            if acceleration > acceleration_epsilon:
                momentum = "accelerating"
            elif acceleration < -acceleration_epsilon:
                momentum = "decelerating"
        elif direction == "down":
            if acceleration < -acceleration_epsilon:
                momentum = "accelerating"
            elif acceleration > acceleration_epsilon:
                momentum = "decelerating"
        else:
            if acceleration > acceleration_epsilon:
                momentum = "accelerating"
            elif acceleration < -acceleration_epsilon:
                momentum = "decelerating"

    if direction == "up":
        if momentum == "accelerating":
            return "up_up", "build", direction, momentum
        if momentum == "decelerating":
            return "up_flat", "consolidate", direction, momentum
        return "up", "progress", direction, momentum

    if direction == "flat":
        if momentum == "accelerating":
            return "flat_up", "rebound_start", direction, momentum
        if momentum == "decelerating":
            return "flat_down", "plateau_risk", direction, momentum
        return "flat", "plateau", direction, momentum

    # direction == "down"
    if momentum == "accelerating":
        return "down_down", "regression", direction, momentum
    if momentum == "decelerating":
        return "down_flat", "recovery", direction, momentum
    return "down", "dip", direction, momentum


def summarize_signal_dynamics(
    points: list[tuple[float, float]],
    *,
    velocity_epsilon: float,
    acceleration_epsilon: float,
) -> dict[str, Any]:
    if not points:
        return {
            "value": None,
            "velocity_per_day": None,
            "velocity_per_week": None,
            "acceleration_per_day2": None,
            "trajectory_code": "unknown",
            "phase": "unknown",
            "direction": "unknown",
            "momentum": "unknown",
            "confidence": 0.0,
            "samples": 0,
        }

    sorted_points = sorted(points, key=lambda item: item[0])
    slopes, accelerations = _derivative_samples(sorted_points)
    velocity = _tail_weighted_average(slopes, window=3)
    acceleration = _tail_weighted_average(accelerations, window=2)
    trajectory_code, phase, direction, momentum = _trajectory_code(
        velocity,
        acceleration,
        velocity_epsilon=velocity_epsilon,
        acceleration_epsilon=acceleration_epsilon,
    )

    slope_strength = min(1.0, len(slopes) / 3.0)
    accel_strength = min(1.0, len(accelerations) / 2.0)
    confidence = min(1.0, (0.7 * slope_strength) + (0.3 * accel_strength))

    return {
        "value": _round_or_none(sorted_points[-1][1], 3),
        "velocity_per_day": _round_or_none(velocity, 6),
        "velocity_per_week": _round_or_none((velocity * 7.0) if velocity is not None else None, 6),
        "acceleration_per_day2": _round_or_none(acceleration, 6),
        "trajectory_code": trajectory_code,
        "phase": phase,
        "direction": direction,
        "momentum": momentum,
        "confidence": round(confidence, 3),
        "samples": len(sorted_points),
    }


def weekly_phase_from_date(value: Any) -> dict[str, Any]:
    parsed = _as_date(value)
    if parsed is None:
        return {
            "day_of_week": None,
            "phase": "unknown",
            "angle_deg": None,
            "bucket_index": None,
        }

    weekday = parsed.weekday()
    angle_deg = (weekday / 7.0) * 360.0
    return {
        "day_of_week": parsed.strftime("%A").lower(),
        "phase": _WEEKLY_PHASE_BY_WEEKDAY[weekday],
        "angle_deg": round(angle_deg, 2),
        "bucket_index": weekday,
    }


def _inv2(m: list[list[float]]) -> list[list[float]]:
    det = m[0][0] * m[1][1] - m[0][1] * m[1][0]
    if abs(det) < 1e-12:
        det = 1e-12
    inv_det = 1.0 / det
    return [
        [m[1][1] * inv_det, -m[0][1] * inv_det],
        [-m[1][0] * inv_det, m[0][0] * inv_det],
    ]


def _matvec(m: list[list[float]], v: list[float]) -> list[float]:
    return [
        m[0][0] * v[0] + m[0][1] * v[1],
        m[1][0] * v[0] + m[1][1] * v[1],
    ]


def _dot(a: list[float], b: list[float]) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    sigma = max(1e-9, sigma)
    z = (x - mu) / (sigma * math.sqrt(2.0))
    return 0.5 * (1.0 + math.erf(z))


def _ci95(mu: float, sigma: float) -> list[float]:
    delta = 1.96 * max(1e-9, sigma)
    return [round(mu - delta, 2), round(mu + delta, 2)]


def _resolve_strength_beta_prior(population_prior: dict[str, Any] | None) -> tuple[float, float, dict[str, Any]]:
    base_mean = 0.0
    base_var = 4.0
    meta = {"applied": False}
    if not isinstance(population_prior, dict):
        return base_mean, base_var, meta

    pop_mean = _as_float(population_prior.get("mean"))
    pop_var = _as_float(population_prior.get("var"))
    blend = _as_float(population_prior.get("blend_weight"))
    if pop_mean is None or pop_var is None or pop_var <= 0.0:
        return base_mean, base_var, meta
    if blend is None:
        blend = 0.35
    blend = min(0.95, max(0.0, blend))

    prior_mean = ((1.0 - blend) * base_mean) + (blend * pop_mean)
    prior_var = ((1.0 - blend) * base_var) + (blend * max(pop_var, 1e-6))
    meta = {
        "applied": True,
        "blend_weight": round(blend, 4),
        "cohort_key": population_prior.get("cohort_key"),
        "target_key": population_prior.get("target_key"),
        "participants_count": population_prior.get("participants_count"),
        "sample_size": population_prior.get("sample_size"),
        "computed_at": population_prior.get("computed_at"),
    }
    return prior_mean, max(prior_var, 1e-6), meta


def _resolve_readiness_prior(
    population_prior: dict[str, Any] | None,
    *,
    default_mean: float,
    default_var: float,
) -> tuple[float, float, dict[str, Any]]:
    meta = {"applied": False}
    if not isinstance(population_prior, dict):
        return default_mean, default_var, meta

    pop_mean = _as_float(population_prior.get("mean"))
    pop_var = _as_float(population_prior.get("var"))
    blend = _as_float(population_prior.get("blend_weight"))
    if pop_mean is None or pop_var is None or pop_var <= 0.0:
        return default_mean, default_var, meta
    if blend is None:
        blend = 0.35
    blend = min(0.95, max(0.0, blend))

    prior_mean = ((1.0 - blend) * default_mean) + (blend * pop_mean)
    prior_var = ((1.0 - blend) * default_var) + (blend * max(pop_var, 1e-6))
    meta = {
        "applied": True,
        "blend_weight": round(blend, 4),
        "cohort_key": population_prior.get("cohort_key"),
        "target_key": population_prior.get("target_key"),
        "participants_count": population_prior.get("participants_count"),
        "sample_size": population_prior.get("sample_size"),
        "computed_at": population_prior.get("computed_at"),
    }
    return prior_mean, max(prior_var, 1e-6), meta


def _closed_form_strength(
    x: list[float],
    y: list[float],
    horizon_days: float,
    slope_plateau_threshold: float,
    *,
    prior_beta_mean: float = 0.0,
    prior_beta_var: float = 4.0,
) -> dict:
    """Closed-form Bayesian linear regression with known-noise approximation."""
    x_mean = sum(x) / len(x)
    x_centered = [xi - x_mean for xi in x]

    # Model: y = alpha + beta*x + eps
    # Prior
    prior_mean = [mean(y), prior_beta_mean]  # alpha, beta
    prior_cov = [[400.0, 0.0], [0.0, max(1e-6, prior_beta_var)]]
    prior_prec = _inv2(prior_cov)

    # Empirical noise estimate with floor.
    y_mu = sum(y) / len(y)
    sample_var = sum((yi - y_mu) ** 2 for yi in y) / max(1, len(y) - 1)
    sigma2 = max(25.0, sample_var)

    # X'X and X'y for [1, x]
    s11 = float(len(x_centered))
    s12 = sum(x_centered)
    s22 = sum(xi * xi for xi in x_centered)
    xtx = [[s11 / sigma2, s12 / sigma2], [s12 / sigma2, s22 / sigma2]]
    xty = [sum(yi for yi in y) / sigma2, sum(xi * yi for xi, yi in zip(x_centered, y)) / sigma2]

    post_prec = [
        [xtx[0][0] + prior_prec[0][0], xtx[0][1] + prior_prec[0][1]],
        [xtx[1][0] + prior_prec[1][0], xtx[1][1] + prior_prec[1][1]],
    ]
    post_cov = _inv2(post_prec)
    rhs = [xty[0] + _dot(prior_prec[0], prior_mean), xty[1] + _dot(prior_prec[1], prior_mean)]
    post_mean = _matvec(post_cov, rhs)

    alpha_mu, beta_mu = post_mean
    alpha_sd = math.sqrt(max(post_cov[0][0], 1e-9))
    beta_sd = math.sqrt(max(post_cov[1][1], 1e-9))

    x_last = x_centered[-1]
    x_future = x_last + horizon_days

    current_mu = alpha_mu + beta_mu * x_last
    current_var = sigma2 + post_cov[0][0] + (x_last * x_last) * post_cov[1][1] + 2 * x_last * post_cov[0][1]
    current_sd = math.sqrt(max(current_var, 1e-9))

    future_mu = alpha_mu + beta_mu * x_future
    future_var = sigma2 + post_cov[0][0] + (x_future * x_future) * post_cov[1][1] + 2 * x_future * post_cov[0][1]
    future_sd = math.sqrt(max(future_var, 1e-9))

    plateau_probability = _normal_cdf(slope_plateau_threshold, beta_mu, beta_sd)
    improving_probability = 1.0 - _normal_cdf(0.0, beta_mu, beta_sd)

    return {
        "engine": "closed_form",
        "trend": {
            "slope_kg_per_day": round(beta_mu, 4),
            "slope_kg_per_week": round(beta_mu * 7.0, 3),
            "slope_ci95": _ci95(beta_mu, beta_sd),
            "plateau_probability": round(plateau_probability, 4),
            "improving_probability": round(improving_probability, 4),
        },
        "estimated_1rm": {
            "mean": round(current_mu, 2),
            "ci95": _ci95(current_mu, current_sd),
        },
        "predicted_1rm": {
            "horizon_days": int(horizon_days),
            "mean": round(future_mu, 2),
            "ci95": _ci95(future_mu, future_sd),
        },
        "diagnostics": {
            "sigma": round(math.sqrt(sigma2), 3),
            "alpha_sd": round(alpha_sd, 4),
            "beta_sd": round(beta_sd, 6),
        },
    }


def _hierarchical_surrogate_strength(
    x: list[float],
    y: list[float],
    horizon_days: float,
    slope_plateau_threshold: float,
    *,
    prior_beta_mean: float = 0.0,
    prior_beta_var: float = 4.0,
) -> dict:
    """Surrogate hierarchical path with deterministic shrinkage.

    This keeps runtime inexpensive while exposing a staging-compatible engine
    contract until a heavier full hierarchical MCMC path is used for offline
    refits.
    """
    base = _closed_form_strength(
        x,
        y,
        horizon_days,
        slope_plateau_threshold,
        prior_beta_mean=prior_beta_mean,
        prior_beta_var=prior_beta_var,
    )
    n = max(1, len(x))
    shrinkage = min(0.7, max(0.15, 4.0 / (n + 4.0)))
    slope = _as_float((base.get("trend") or {}).get("slope_kg_per_day")) or 0.0
    shrunk_slope = ((1.0 - shrinkage) * slope) + (shrinkage * prior_beta_mean)
    slope_sd = ((base.get("trend") or {}).get("slope_ci95") or [0.0, 0.0])
    if isinstance(slope_sd, list) and len(slope_sd) == 2:
        ci_center = shrunk_slope
        ci_half_width = max(0.0, abs(float(slope_sd[1]) - float(slope_sd[0])) / 2.0) * (1.0 + 0.25 * shrinkage)
        slope_ci95 = [round(ci_center - ci_half_width, 2), round(ci_center + ci_half_width, 2)]
    else:
        slope_ci95 = [round(shrunk_slope - 0.05, 2), round(shrunk_slope + 0.05, 2)]

    estimated = dict(base.get("estimated_1rm") or {})
    predicted = dict(base.get("predicted_1rm") or {})
    est_mean = _as_float(estimated.get("mean")) or 0.0
    adjustment = (shrunk_slope - slope) * (horizon_days / 2.0)
    predicted_mean = (_as_float(predicted.get("mean")) or est_mean) + adjustment
    predicted["mean"] = round(predicted_mean, 2)

    trend = dict(base.get("trend") or {})
    trend["slope_kg_per_day"] = round(shrunk_slope, 4)
    trend["slope_kg_per_week"] = round(shrunk_slope * 7.0, 3)
    trend["slope_ci95"] = slope_ci95
    trend["plateau_probability"] = round(
        _normal_cdf(slope_plateau_threshold, shrunk_slope, max(0.005, abs(slope_ci95[1] - slope_ci95[0]) / 3.92)),
        4,
    )
    trend["improving_probability"] = round(
        1.0 - _normal_cdf(0.0, shrunk_slope, max(0.005, abs(slope_ci95[1] - slope_ci95[0]) / 3.92)),
        4,
    )

    diagnostics = dict(base.get("diagnostics") or {})
    diagnostics["surrogate"] = True
    diagnostics["hierarchical_shrinkage"] = round(shrinkage, 4)
    diagnostics["prior_beta_mean"] = round(prior_beta_mean, 4)
    diagnostics["prior_beta_var"] = round(prior_beta_var, 6)
    diagnostics["fallback_reason"] = "surrogate_runtime_stage"

    return {
        "engine": "hierarchical_bayes_surrogate",
        "trend": trend,
        "estimated_1rm": estimated,
        "predicted_1rm": predicted,
        "diagnostics": diagnostics,
    }


def _pymc_strength(
    x: list[float],
    y: list[float],
    horizon_days: float,
    slope_plateau_threshold: float,
    *,
    prior_beta_mean: float = 0.0,
    prior_beta_var: float = 4.0,
) -> dict | None:
    """PyMC posterior sampling path. Returns None on unavailable/runtime failure."""
    try:
        import arviz as az
        import numpy as np
        import pymc as pm
    except Exception as exc:
        logger.warning("PyMC path unavailable (%s); using closed-form strength inference", exc)
        return None

    try:
        xa = np.array(x, dtype=float)
        ya = np.array(y, dtype=float)
        x_mean = float(xa.mean())
        xc = xa - x_mean

        with pm.Model():
            alpha = pm.Normal("alpha", mu=float(ya.mean()), sigma=30.0)
            beta = pm.Normal("beta", mu=prior_beta_mean, sigma=math.sqrt(max(1e-6, prior_beta_var)))
            sigma = pm.HalfNormal("sigma", sigma=10.0)
            mu = alpha + beta * xc
            pm.Normal("obs", mu=mu, sigma=sigma, observed=ya)
            trace = pm.sample(
                draws=600,
                tune=600,
                chains=2,
                cores=1,
                progressbar=False,
                random_seed=42,
                target_accept=0.9,
            )

        alpha_samples = trace.posterior["alpha"].values.flatten()
        beta_samples = trace.posterior["beta"].values.flatten()
        sigma_samples = trace.posterior["sigma"].values.flatten()

        x_last = float(xc[-1])
        x_future = x_last + horizon_days

        current_samples = alpha_samples + beta_samples * x_last
        future_samples = alpha_samples + beta_samples * x_future

        slope_mu = float(beta_samples.mean())
        plateau_probability = float((beta_samples <= slope_plateau_threshold).mean())
        improving_probability = float((beta_samples > 0.0).mean())

        rhat = float(az.rhat(trace).to_array().max().item())
        ess = float(az.ess(trace).to_array().min().item())

        def q(values, p):
            return float(np.quantile(values, p))

        return {
            "engine": "pymc",
            "trend": {
                "slope_kg_per_day": round(slope_mu, 4),
                "slope_kg_per_week": round(slope_mu * 7.0, 3),
                "slope_ci95": [round(q(beta_samples, 0.025), 4), round(q(beta_samples, 0.975), 4)],
                "plateau_probability": round(plateau_probability, 4),
                "improving_probability": round(improving_probability, 4),
            },
            "estimated_1rm": {
                "mean": round(float(current_samples.mean()), 2),
                "ci95": [round(q(current_samples, 0.025), 2), round(q(current_samples, 0.975), 2)],
            },
            "predicted_1rm": {
                "horizon_days": int(horizon_days),
                "mean": round(float(future_samples.mean()), 2),
                "ci95": [round(q(future_samples, 0.025), 2), round(q(future_samples, 0.975), 2)],
            },
            "diagnostics": {
                "rhat": round(rhat, 4),
                "ess_min": round(ess, 1),
                "sigma_mean": round(float(sigma_samples.mean()), 3),
                "draws": int(alpha_samples.size),
            },
        }
    except Exception as exc:  # Sampling/runtime failures fallback safely.
        logger.warning("PyMC strength inference failed (%s); using closed-form fallback", exc)
        return None


def run_strength_inference(
    points: list[tuple[float, float]],
    *,
    population_prior: dict[str, Any] | None = None,
) -> dict:
    """Run strength inference over (day_offset, estimated_1rm) points."""
    prior_beta_mean, prior_beta_var, population_prior_meta = _resolve_strength_beta_prior(
        population_prior
    )

    dynamics = summarize_signal_dynamics(
        points,
        velocity_epsilon=float(os.environ.get("KURA_STRENGTH_DERIVATIVE_VELOCITY_EPS", "0.03")),
        acceleration_epsilon=float(
            os.environ.get("KURA_STRENGTH_DERIVATIVE_ACCELERATION_EPS", "0.01")
        ),
    )

    if len(points) < 3:
        return {
            "engine": "none",
            "status": "insufficient_data",
            "required_points": 3,
            "observed_points": len(points),
            "dynamics": dynamics,
            "population_prior": population_prior_meta,
        }

    x = [p[0] for p in points]
    y = [p[1] for p in points]
    horizon_days = float(int(os.environ.get("KURA_BAYES_FORECAST_DAYS", "28")))
    slope_plateau_threshold = float(os.environ.get("KURA_BAYES_PLATEAU_SLOPE_PER_DAY", "0.02"))
    preferred_engine = os.environ.get("KURA_BAYES_ENGINE", "pymc").strip().lower()

    result: dict[str, Any]
    if preferred_engine == "hierarchical_bayes":
        result = _hierarchical_surrogate_strength(
            x,
            y,
            horizon_days,
            slope_plateau_threshold,
            prior_beta_mean=prior_beta_mean,
            prior_beta_var=prior_beta_var,
        )
    elif preferred_engine == "pymc":
        pymc_result = _pymc_strength(
            x,
            y,
            horizon_days,
            slope_plateau_threshold,
            prior_beta_mean=prior_beta_mean,
            prior_beta_var=prior_beta_var,
        )
        if pymc_result is not None:
            result = pymc_result
        else:
            result = _closed_form_strength(
                x,
                y,
                horizon_days,
                slope_plateau_threshold,
                prior_beta_mean=prior_beta_mean,
                prior_beta_var=prior_beta_var,
            )
    else:
        result = _closed_form_strength(
            x,
            y,
            horizon_days,
            slope_plateau_threshold,
            prior_beta_mean=prior_beta_mean,
            prior_beta_var=prior_beta_var,
        )

    trend = result.get("trend") or {}
    slope_ci95 = trend.get("slope_ci95")
    if isinstance(slope_ci95, list) and len(slope_ci95) == 2:
        low = _as_float(slope_ci95[0])
        high = _as_float(slope_ci95[1])
        if low is not None and high is not None:
            dynamics["model_velocity_ci95"] = [round(low, 6), round(high, 6)]

    modeled_velocity = _as_float(trend.get("slope_kg_per_day"))
    if modeled_velocity is not None:
        dynamics["model_velocity_per_day"] = round(modeled_velocity, 6)
        dynamics["model_velocity_per_week"] = round(modeled_velocity * 7.0, 6)

    result["dynamics"] = dynamics
    result["population_prior"] = population_prior_meta
    return result


def run_readiness_inference(
    observations: list[float],
    *,
    day_offsets: list[float] | None = None,
    observation_variances: list[float] | None = None,
    population_prior: dict[str, Any] | None = None,
) -> dict:
    """Normal-Normal Bayesian update for readiness score [0, 1]."""
    points: list[tuple[float, float]] = []
    if day_offsets and len(day_offsets) == len(observations):
        points = [
            (float(day_offsets[idx]), float(observations[idx]))
            for idx in range(len(observations))
        ]
    else:
        points = [(float(idx), float(value)) for idx, value in enumerate(observations)]
    dynamics = summarize_signal_dynamics(
        points,
        velocity_epsilon=float(os.environ.get("KURA_READINESS_DERIVATIVE_VELOCITY_EPS", "0.015")),
        acceleration_epsilon=float(
            os.environ.get("KURA_READINESS_DERIVATIVE_ACCELERATION_EPS", "0.008")
        ),
    )

    if len(observations) < 5:
        return {
            "engine": "none",
            "status": "insufficient_data",
            "required_points": 5,
            "observed_points": len(observations),
            "dynamics": dynamics,
            "population_prior": {"applied": False},
        }

    default_prior_mean = float(os.environ.get("KURA_READINESS_PRIOR_MEAN", "0.6"))
    default_prior_var = float(os.environ.get("KURA_READINESS_PRIOR_VAR", "0.04"))  # sd ~0.2
    prior_mean, prior_var, population_prior_meta = _resolve_readiness_prior(
        population_prior,
        default_mean=default_prior_mean,
        default_var=default_prior_var,
    )

    weighted = False
    observation_var_values: list[float] = []
    if observation_variances and len(observation_variances) == len(observations):
        for item in observation_variances:
            parsed = _as_float(item)
            if parsed is None:
                continue
            observation_var_values.append(max(0.005, float(parsed)))
        weighted = len(observation_var_values) == len(observations)

    if weighted:
        precisions = [1.0 / var for var in observation_var_values]
        precision_sum = sum(precisions)
        obs_mean = sum(
            observations[idx] * precisions[idx] for idx in range(len(observations))
        ) / max(precision_sum, 1e-9)
        obs_var = sum(
            precisions[idx] * ((observations[idx] - obs_mean) ** 2)
            for idx in range(len(observations))
        ) / max(precision_sum, 1e-9)
        obs_var = max(0.005, obs_var)

        post_precision = (1.0 / prior_var) + precision_sum
        post_var = 1.0 / max(post_precision, 1e-9)
        post_mean = post_var * (
            (prior_mean / prior_var)
            + sum(
                observations[idx] / observation_var_values[idx]
                for idx in range(len(observations))
            )
        )
        effective_observations = (precision_sum**2) / max(
            sum(precision * precision for precision in precisions),
            1e-9,
        )
    else:
        obs_mean = sum(observations) / len(observations)
        obs_var = sum((x - obs_mean) ** 2 for x in observations) / max(1, len(observations) - 1)
        obs_var = max(0.005, obs_var)

        post_precision = (1.0 / prior_var) + (len(observations) / obs_var)
        post_var = 1.0 / post_precision
        post_mean = post_var * (
            (prior_mean / prior_var) + (len(observations) * obs_mean / obs_var)
        )
        effective_observations = float(len(observations))
    post_sd = math.sqrt(post_var)

    latest = observations[-1]
    short_term = 0.7 * latest + 0.3 * post_mean

    state = "moderate"
    if short_term >= 0.72:
        state = "high"
    elif short_term <= 0.45:
        state = "low"

    result = {
        "engine": "normal_normal",
        "status": "ok",
        "readiness_today": {
            "mean": round(short_term, 3),
            "ci95": _ci95(short_term, post_sd),
            "state": state,
        },
        "baseline": {
            "posterior_mean": round(post_mean, 3),
            "posterior_ci95": _ci95(post_mean, post_sd),
            "observations": len(observations),
        },
        "diagnostics": {
            "obs_var": round(obs_var, 5),
            "prior_mean": prior_mean,
            "prior_var": prior_var,
            "effective_observations": round(effective_observations, 4),
            "weighted_observations": weighted,
            "mean_observation_variance": (
                round(sum(observation_var_values) / len(observation_var_values), 6)
                if observation_var_values
                else None
            ),
            "day_span_days": (
                round(points[-1][0] - points[0][0], 3) if len(points) >= 2 else 0.0
            ),
        },
    }

    readiness_today = result.get("readiness_today") or {}
    today_mean = _as_float(readiness_today.get("mean"))
    if today_mean is not None:
        dynamics["value"] = round(today_mean, 3)
    state = readiness_today.get("state")
    if isinstance(state, str) and state:
        dynamics["state"] = state

    result["dynamics"] = dynamics
    result["population_prior"] = population_prior_meta
    return result
