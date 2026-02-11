"""Bayesian inference utilities for strength/readiness projections."""

from __future__ import annotations

import logging
import math
import os
from statistics import mean

logger = logging.getLogger(__name__)


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


def _closed_form_strength(
    x: list[float],
    y: list[float],
    horizon_days: float,
    slope_plateau_threshold: float,
) -> dict:
    """Closed-form Bayesian linear regression with known-noise approximation."""
    x_mean = sum(x) / len(x)
    x_centered = [xi - x_mean for xi in x]

    # Model: y = alpha + beta*x + eps
    # Prior
    prior_mean = [mean(y), 0.0]  # alpha, beta
    prior_cov = [[400.0, 0.0], [0.0, 4.0]]  # broad prior
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


def _pymc_strength(
    x: list[float],
    y: list[float],
    horizon_days: float,
    slope_plateau_threshold: float,
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
            beta = pm.Normal("beta", mu=0.0, sigma=2.0)
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


def run_strength_inference(points: list[tuple[float, float]]) -> dict:
    """Run strength inference over (day_offset, estimated_1rm) points."""
    if len(points) < 3:
        return {
            "engine": "none",
            "status": "insufficient_data",
            "required_points": 3,
            "observed_points": len(points),
        }

    x = [p[0] for p in points]
    y = [p[1] for p in points]
    horizon_days = float(int(os.environ.get("KURA_BAYES_FORECAST_DAYS", "28")))
    slope_plateau_threshold = float(os.environ.get("KURA_BAYES_PLATEAU_SLOPE_PER_DAY", "0.02"))
    preferred_engine = os.environ.get("KURA_BAYES_ENGINE", "pymc").strip().lower()

    if preferred_engine == "pymc":
        pymc_result = _pymc_strength(x, y, horizon_days, slope_plateau_threshold)
        if pymc_result is not None:
            return pymc_result

    return _closed_form_strength(x, y, horizon_days, slope_plateau_threshold)


def run_readiness_inference(observations: list[float]) -> dict:
    """Normal-Normal Bayesian update for readiness score [0, 1]."""
    if len(observations) < 5:
        return {
            "engine": "none",
            "status": "insufficient_data",
            "required_points": 5,
            "observed_points": len(observations),
        }

    prior_mean = float(os.environ.get("KURA_READINESS_PRIOR_MEAN", "0.6"))
    prior_var = float(os.environ.get("KURA_READINESS_PRIOR_VAR", "0.04"))  # sd ~0.2

    obs_mean = sum(observations) / len(observations)
    obs_var = sum((x - obs_mean) ** 2 for x in observations) / max(1, len(observations) - 1)
    obs_var = max(0.005, obs_var)

    post_precision = (1.0 / prior_var) + (len(observations) / obs_var)
    post_var = 1.0 / post_precision
    post_mean = post_var * ((prior_mean / prior_var) + (len(observations) * obs_mean / obs_var))
    post_sd = math.sqrt(post_var)

    latest = observations[-1]
    short_term = 0.7 * latest + 0.3 * post_mean

    state = "moderate"
    if short_term >= 0.72:
        state = "high"
    elif short_term <= 0.45:
        state = "low"

    return {
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
        },
    }
