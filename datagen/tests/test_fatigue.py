"""Tests for the fatigue/recovery model."""

from datetime import date

from datagen.fatigue import (
    compute_effective_energy,
    compute_performance_modifier,
    compute_training_stress,
    should_trigger_injury,
    update_fatigue_rest_day,
    update_fatigue_training_day,
    update_sleep_debt,
    FatigueSnapshot,
)
from datagen.models import AthleteState


def _fresh_state(**overrides) -> AthleteState:
    defaults = dict(
        day=date(2025, 10, 1),
        fatigue=0.0,
        current_1rms={"barbell_back_squat": 120.0},
        bodyweight_kg=82.0,
        sleep_debt=0.0,
        energy_baseline=7.0,
        soreness={},
        mesocycle_week=1,
        mesocycle_day=0,
        weekly_volume={},
        training_day_index=0,
        aliases_created=set(),
        total_days=0,
    )
    defaults.update(overrides)
    return AthleteState(**defaults)


def test_training_stress_range():
    # Light session
    light = compute_training_stress(8, 0.70)
    assert 0.0 < light < 0.2

    # Heavy session
    heavy = compute_training_stress(20, 0.90)
    assert heavy > light


def test_training_stress_caps_at_max():
    # Even extreme values shouldn't exceed ~0.5
    extreme = compute_training_stress(30, 1.0)
    assert extreme <= 0.5


def test_fatigue_decays_on_rest():
    state = _fresh_state(fatigue=0.5)
    update_fatigue_rest_day(state)
    assert state.fatigue == 0.5 * 0.80  # 0.4


def test_fatigue_accumulates_with_training():
    state = _fresh_state(fatigue=0.2)
    update_fatigue_training_day(state, total_sets=15, avg_intensity=0.80)
    assert state.fatigue > 0.2  # Should increase


def test_fatigue_never_exceeds_1():
    state = _fresh_state(fatigue=0.95)
    update_fatigue_training_day(state, total_sets=25, avg_intensity=1.0)
    assert state.fatigue <= 1.0


def test_fatigue_never_below_0():
    state = _fresh_state(fatigue=0.01)
    update_fatigue_rest_day(state)
    assert state.fatigue >= 0.0


def test_sleep_debt_accumulates():
    state = _fresh_state(sleep_debt=0.0)
    update_sleep_debt(state, target_hours=8.0, actual_hours=6.0)
    assert state.sleep_debt == 2.0  # 0 * 0.7 + 2


def test_sleep_debt_decays():
    state = _fresh_state(sleep_debt=3.0)
    update_sleep_debt(state, target_hours=8.0, actual_hours=8.0)
    # 3.0 * 0.7 + 0 = 2.1
    assert abs(state.sleep_debt - 2.1) < 0.01


def test_sleep_surplus_doesnt_create_negative_debt():
    state = _fresh_state(sleep_debt=0.0)
    update_sleep_debt(state, target_hours=7.0, actual_hours=9.0)
    assert state.sleep_debt == 0.0  # surplus doesn't go negative


def test_energy_fresh_athlete():
    state = _fresh_state(fatigue=0.0, sleep_debt=0.0)
    energy = compute_effective_energy(state)
    assert energy == 7.0


def test_energy_drops_with_fatigue():
    state = _fresh_state(fatigue=0.8, sleep_debt=0.0)
    energy = compute_effective_energy(state)
    assert energy < 7.0


def test_energy_drops_with_sleep_debt():
    state = _fresh_state(fatigue=0.0, sleep_debt=3.0)
    energy = compute_effective_energy(state)
    assert energy < 7.0


def test_energy_clamped():
    state = _fresh_state(fatigue=1.0, sleep_debt=5.0)
    energy = compute_effective_energy(state)
    assert 1.0 <= energy <= 10.0


def test_performance_modifier_fresh():
    state = _fresh_state(fatigue=0.0, sleep_debt=0.0)
    mod = compute_performance_modifier(state)
    assert mod == 1.0


def test_performance_modifier_fatigued():
    state = _fresh_state(fatigue=0.8, sleep_debt=2.0)
    mod = compute_performance_modifier(state)
    assert mod < 1.0
    assert mod >= 0.70


def test_performance_modifier_never_above_105():
    state = _fresh_state(fatigue=0.0, sleep_debt=0.0)
    mod = compute_performance_modifier(state)
    assert mod <= 1.05


def test_injury_impossible_at_low_fatigue():
    state = _fresh_state(fatigue=0.3)
    # Even with rng_value=0.0 (always triggers), low fatigue prevents injury
    assert not should_trigger_injury(state, 0.0)


def test_injury_possible_at_high_fatigue():
    state = _fresh_state(fatigue=0.9, sleep_debt=2.0)
    # With rng_value=0.0 (always triggers), high fatigue should allow it
    assert should_trigger_injury(state, 0.0)


def test_injury_probabilistic():
    state = _fresh_state(fatigue=0.8, sleep_debt=1.0)
    # Very high rng_value should not trigger
    assert not should_trigger_injury(state, 0.99)


def test_cross_domain_correlation():
    """The key feature: bad sleep → less energy → worse performance."""
    good_state = _fresh_state(fatigue=0.2, sleep_debt=0.0)
    bad_state = _fresh_state(fatigue=0.6, sleep_debt=3.0)

    good_energy = compute_effective_energy(good_state)
    bad_energy = compute_effective_energy(bad_state)
    assert good_energy > bad_energy

    good_perf = compute_performance_modifier(good_state)
    bad_perf = compute_performance_modifier(bad_state)
    assert good_perf > bad_perf


def test_fatigue_snapshot():
    state = _fresh_state(fatigue=0.3, sleep_debt=1.0)
    snap = FatigueSnapshot.from_state(state)
    assert snap.fatigue == 0.3
    assert snap.sleep_debt == 1.0
    assert 1.0 <= snap.energy <= 10.0
    assert 0.70 <= snap.performance_modifier <= 1.05
