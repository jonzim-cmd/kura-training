"""Cross-domain fatigue and recovery model.

Central state machine that ties sleep, training stress, and recovery together.
This creates the realistic cross-domain correlations:
  - Bad sleep → low energy → fewer reps → higher RPE
  - Deload week → fatigue drops → energy rises → stronger performance
  - Accumulated fatigue → potential injury
"""

from __future__ import annotations

from dataclasses import dataclass

from datagen.models import AthleteState


# Decay rates
FATIGUE_DECAY_REST = 0.80  # 20% recovery on rest days
FATIGUE_DECAY_TRAINING = 0.90  # 10% recovery on training days (training adds stress)

# Sleep debt parameters
SLEEP_DEBT_DECAY = 0.70  # 30% of sleep debt resolved per night

# Energy parameters
ENERGY_BASE = 7.0
ENERGY_FATIGUE_WEIGHT = -0.3
ENERGY_SLEEP_DEBT_WEIGHT = -0.4


def compute_training_stress(
    total_sets: int,
    avg_intensity: float,
    max_sets_per_session: int = 25,
) -> float:
    """Compute training stress from a session.

    Args:
        total_sets: Total working sets in the session
        avg_intensity: Average %1RM across working sets (0-1)
        max_sets_per_session: Normalization constant

    Returns:
        Stress value in [0, ~0.5] range
    """
    volume_factor = min(1.0, total_sets / max_sets_per_session)
    intensity_factor = avg_intensity  # already 0-1
    return volume_factor * intensity_factor * 0.5


def update_fatigue_rest_day(state: AthleteState) -> None:
    """Update fatigue on a rest day (no training)."""
    state.fatigue = max(0.0, state.fatigue * FATIGUE_DECAY_REST)


def update_fatigue_training_day(
    state: AthleteState,
    total_sets: int,
    avg_intensity: float,
) -> None:
    """Update fatigue after a training session."""
    stress = compute_training_stress(total_sets, avg_intensity)
    state.fatigue = min(1.0, state.fatigue * FATIGUE_DECAY_TRAINING + stress)


def update_sleep_debt(state: AthleteState, target_hours: float, actual_hours: float) -> None:
    """Update accumulated sleep debt."""
    deficit = max(0.0, target_hours - actual_hours)
    state.sleep_debt = state.sleep_debt * SLEEP_DEBT_DECAY + deficit


def compute_effective_energy(state: AthleteState, noise: float = 0.0) -> float:
    """Compute current energy level (1-10 scale).

    Args:
        state: Current athlete state
        noise: Random noise to add (typically ±0.5)

    Returns:
        Energy level clamped to [1, 10]
    """
    energy = (
        ENERGY_BASE
        + ENERGY_FATIGUE_WEIGHT * state.fatigue * 10  # fatigue 0-1 → 0-3 impact
        + ENERGY_SLEEP_DEBT_WEIGHT * state.sleep_debt  # sleep_debt in hours
        + noise
    )
    return max(1.0, min(10.0, energy))


def compute_performance_modifier(state: AthleteState) -> float:
    """Compute performance modifier for training.

    Returns a multiplier (typically 0.75 - 1.05) that affects
    how many reps an athlete can do at a given weight.

    High fatigue and sleep debt reduce performance.
    """
    modifier = 1.0 - 0.15 * state.fatigue - 0.10 * min(3.0, state.sleep_debt)
    return max(0.70, min(1.05, modifier))


def should_trigger_injury(state: AthleteState, rng_value: float) -> bool:
    """Check if accumulated fatigue should trigger a minor injury.

    Higher fatigue + sleep debt increases probability.

    Args:
        state: Current athlete state
        rng_value: Random value in [0, 1)

    Returns:
        True if an injury event should be generated
    """
    # Only possible when fatigue is quite high
    if state.fatigue < 0.7:
        return False
    # Base probability per day when fatigued: ~2%
    # Increases with fatigue and sleep debt
    probability = 0.02 * state.fatigue + 0.01 * min(2.0, state.sleep_debt)
    return rng_value < probability


@dataclass(frozen=True)
class FatigueSnapshot:
    """Immutable snapshot of fatigue-related values for a given day."""

    fatigue: float
    sleep_debt: float
    energy: float
    performance_modifier: float

    @classmethod
    def from_state(cls, state: AthleteState, energy_noise: float = 0.0) -> FatigueSnapshot:
        return cls(
            fatigue=state.fatigue,
            sleep_debt=state.sleep_debt,
            energy=compute_effective_energy(state, energy_noise),
            performance_modifier=compute_performance_modifier(state),
        )
