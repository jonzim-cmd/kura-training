"""Tests for the periodization model."""

from datagen.periodization import (
    rpe_to_percentage,
    percentage_to_weight,
    working_sets_for_week,
    target_rpe_for_week,
    warmup_percentages,
    is_deload_week,
    RPE_TABLE,
)


def test_rpe_table_exact_lookup():
    # Exact entries from the table
    assert rpe_to_percentage(5, 8.0) == 0.83
    assert rpe_to_percentage(1, 10.0) == 1.00
    assert rpe_to_percentage(10, 9.0) == 0.75


def test_rpe_interpolation_between_rpe_values():
    # RPE 8.5 at 5 reps should be between 0.83 (RPE 8) and 0.86 (RPE 9)
    pct = rpe_to_percentage(5, 8.5)
    assert 0.83 < pct < 0.86


def test_rpe_interpolation_between_rep_counts():
    # 7 reps at RPE 8 should be between 6-rep and 8-rep values
    pct_6 = rpe_to_percentage(6, 8.0)
    pct_8 = rpe_to_percentage(8, 8.0)
    pct_7 = rpe_to_percentage(7, 8.0)
    assert pct_8 < pct_7 < pct_6


def test_rpe_clamping():
    # Values outside range should clamp
    pct_low = rpe_to_percentage(5, 5.0)  # Clamped to 7
    assert pct_low == rpe_to_percentage(5, 7.0)

    pct_high = rpe_to_percentage(5, 12.0)  # Clamped to 10
    assert pct_high == rpe_to_percentage(5, 10.0)


def test_percentage_to_weight():
    # 100kg * 0.83 = 83 → rounds to 82.5
    assert percentage_to_weight(100, 0.83) == 82.5
    # 120kg * 0.86 = 103.2 → rounds to 102.5
    assert percentage_to_weight(120, 0.86) == 102.5
    # Never below 2.5
    assert percentage_to_weight(5, 0.1) >= 2.5


def test_volume_multipliers_sum():
    # Deload should be lowest, peak should be highest
    sets_deload = working_sets_for_week(4)
    sets_peak = working_sets_for_week(3)
    assert sets_deload < sets_peak


def test_working_sets_range():
    for week in range(1, 5):
        sets = working_sets_for_week(week)
        assert 2 <= sets <= 5


def test_target_rpe_progression():
    # RPE should build through weeks 1-3, then drop for deload
    assert target_rpe_for_week(1) < target_rpe_for_week(2) < target_rpe_for_week(3)
    assert target_rpe_for_week(4) < target_rpe_for_week(1)


def test_warmup_percentages():
    wu = warmup_percentages()
    assert len(wu) == 3
    assert wu == sorted(wu)  # ascending
    assert all(0.3 < p < 0.8 for p in wu)


def test_deload_only_week_4():
    assert not is_deload_week(1)
    assert not is_deload_week(2)
    assert not is_deload_week(3)
    assert is_deload_week(4)


def test_higher_rpe_means_higher_percentage():
    """At same rep count, higher RPE = closer to 1RM."""
    for reps in [1, 3, 5, 8, 10]:
        pct_7 = rpe_to_percentage(reps, 7.0)
        pct_8 = rpe_to_percentage(reps, 8.0)
        pct_9 = rpe_to_percentage(reps, 9.0)
        pct_10 = rpe_to_percentage(reps, 10.0)
        assert pct_7 < pct_8 < pct_9 < pct_10, f"Failed at {reps} reps"


def test_more_reps_means_lower_percentage():
    """At same RPE, more reps = lower %1RM."""
    for rpe in [7, 8, 9, 10]:
        pct_1 = rpe_to_percentage(1, rpe)
        pct_5 = rpe_to_percentage(5, rpe)
        pct_10 = rpe_to_percentage(10, rpe)
        assert pct_1 > pct_5 > pct_10, f"Failed at RPE {rpe}"
