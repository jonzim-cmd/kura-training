"""Mesocycle periodization model and RPE/percentage tables.

Implements a 4-week mesocycle with volume/intensity waves and
Tuchscherer-style RPE ↔ %1RM lookup for realistic weight selection.
"""

from __future__ import annotations

import math

# Tuchscherer RPE table: (reps, rpe) → %1RM
# Interpolation-friendly — covers the ranges we generate.
RPE_TABLE: dict[tuple[int, int], float] = {
    (1, 10): 1.00, (1, 9): 0.96, (1, 8): 0.92, (1, 7): 0.88,
    (2, 10): 0.97, (2, 9): 0.94, (2, 8): 0.90, (2, 7): 0.87,
    (3, 10): 0.94, (3, 9): 0.91, (3, 8): 0.88, (3, 7): 0.84,
    (4, 10): 0.92, (4, 9): 0.89, (4, 8): 0.86, (4, 7): 0.82,
    (5, 10): 0.89, (5, 9): 0.86, (5, 8): 0.83, (5, 7): 0.80,
    (6, 10): 0.87, (6, 9): 0.84, (6, 8): 0.81, (6, 7): 0.78,
    (8, 10): 0.83, (8, 9): 0.80, (8, 8): 0.77, (8, 7): 0.74,
    (10, 10): 0.78, (10, 9): 0.75, (10, 8): 0.72, (10, 7): 0.69,
    (12, 10): 0.74, (12, 9): 0.71, (12, 8): 0.68, (12, 7): 0.65,
    (15, 10): 0.69, (15, 9): 0.66, (15, 8): 0.63, (15, 7): 0.60,
    (20, 10): 0.62, (20, 9): 0.59, (20, 8): 0.56, (20, 7): 0.53,
}

# Available rep counts in the table for interpolation
_REP_KEYS = sorted(set(r for r, _ in RPE_TABLE))


def _lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation between a and b."""
    return a + (b - a) * t


def rpe_to_percentage(reps: int, rpe: float) -> float:
    """Look up %1RM for given reps and RPE, with linear interpolation.

    Returns a value between 0.0 and 1.0.
    """
    rpe_clamped = max(7.0, min(10.0, rpe))
    reps_clamped = max(1, min(20, reps))

    # Find bounding rep counts
    lower_reps = max(r for r in _REP_KEYS if r <= reps_clamped)
    upper_reps = min(r for r in _REP_KEYS if r >= reps_clamped)

    rpe_floor = int(math.floor(rpe_clamped))
    rpe_ceil = int(math.ceil(rpe_clamped))
    rpe_frac = rpe_clamped - rpe_floor

    if rpe_floor < 7:
        rpe_floor = 7
    if rpe_ceil > 10:
        rpe_ceil = 10

    def _lookup(r: int, p: int) -> float:
        return RPE_TABLE.get((r, p), RPE_TABLE.get((r, max(7, min(10, p))), 0.75))

    # Bilinear interpolation: reps × RPE
    if lower_reps == upper_reps:
        val_low = _lookup(lower_reps, rpe_floor)
        val_high = _lookup(lower_reps, rpe_ceil)
        return _lerp(val_low, val_high, rpe_frac)

    rep_frac = (reps_clamped - lower_reps) / (upper_reps - lower_reps)

    val_ll = _lookup(lower_reps, rpe_floor)
    val_lh = _lookup(lower_reps, rpe_ceil)
    val_ul = _lookup(upper_reps, rpe_floor)
    val_uh = _lookup(upper_reps, rpe_ceil)

    low = _lerp(val_ll, val_lh, rpe_frac)
    high = _lerp(val_ul, val_uh, rpe_frac)
    return _lerp(low, high, rep_frac)


def percentage_to_weight(one_rm: float, percentage: float) -> float:
    """Convert %1RM to actual weight, rounded to 2.5 kg increments."""
    raw = one_rm * percentage
    return max(2.5, round(raw / 2.5) * 2.5)


# Mesocycle volume multipliers per week (4-week cycle)
# Week 1: moderate, Week 2: building, Week 3: peak, Week 4: deload
VOLUME_MULTIPLIERS: dict[int, float] = {
    1: 0.85,
    2: 0.90,
    3: 1.00,
    4: 0.60,
}

# Target RPE per mesocycle week
TARGET_RPE: dict[int, float] = {
    1: 7.5,
    2: 8.0,
    3: 8.5,
    4: 7.0,
}

# Working sets per exercise by mesocycle week (before volume multiplier)
BASE_WORKING_SETS = 4


def working_sets_for_week(mesocycle_week: int) -> int:
    """Number of working sets per exercise for the given mesocycle week."""
    base = BASE_WORKING_SETS
    multiplier = VOLUME_MULTIPLIERS.get(mesocycle_week, 0.85)
    return max(2, round(base * multiplier))


def target_rpe_for_week(mesocycle_week: int) -> float:
    """Target RPE for working sets in the given mesocycle week."""
    return TARGET_RPE.get(mesocycle_week, 8.0)


def warmup_percentages() -> list[float]:
    """Return warmup set percentages of the working weight.

    Returns 2-3 warmup sets at 40%, 55%, 70% of working weight.
    """
    return [0.40, 0.55, 0.70]


def is_deload_week(mesocycle_week: int) -> bool:
    return mesocycle_week == 4
