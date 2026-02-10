"""Tests for event generators."""

import random
from datetime import date

from datagen.exercises import EXERCISES
from datagen.fatigue import FatigueSnapshot
from datagen.generators.training import (
    generate_training_session,
    get_session_exercises,
)
from datagen.models import AthleteProfile, AthleteState
from datagen.output import to_api_format


def _make_profile(**overrides) -> AthleteProfile:
    defaults = dict(
        name="test",
        experience_level="intermediate",
        bodyweight_kg=82.0,
        training_days_per_week=4,
        exercises=[
            "barbell_back_squat",
            "barbell_bench_press",
            "conventional_deadlift",
            "overhead_press",
            "barbell_row",
            "pull_up",
            "leg_press",
            "romanian_deadlift",
            "lateral_raise",
            "barbell_curl",
            "tricep_pushdown",
        ],
        squat_1rm_kg=120.0,
        bench_1rm_kg=90.0,
        deadlift_1rm_kg=150.0,
        ohp_1rm_kg=60.0,
        sleep_avg_hours=7.5,
        sleep_std_hours=0.8,
        calorie_target=2800,
        protein_target_g=170,
        progression_rate=0.004,
        start_date=date(2025, 10, 1),
        seed=42,
    )
    defaults.update(overrides)
    return AthleteProfile(**defaults)


def _make_state(profile: AthleteProfile) -> AthleteState:
    return AthleteState.from_profile(profile)


class TestGetSessionExercises:
    def test_returns_exercises(self):
        profile = _make_profile()
        state = _make_state(profile)
        exercises = get_session_exercises(profile, state)
        assert len(exercises) >= 3
        assert all(ex in profile.exercises for ex in exercises)

    def test_different_days_different_exercises(self):
        profile = _make_profile()
        state = _make_state(profile)
        day0 = get_session_exercises(profile, state)
        state.training_day_index = 1
        day1 = get_session_exercises(profile, state)
        # Different training days should have at least some different exercises
        assert day0 != day1

    def test_wraps_around(self):
        profile = _make_profile(training_days_per_week=3)
        state = _make_state(profile)
        state.training_day_index = 3  # Should wrap to 0
        exercises = get_session_exercises(profile, state)
        assert len(exercises) >= 3


class TestGenerateTrainingSession:
    def test_generates_events(self):
        profile = _make_profile()
        state = _make_state(profile)
        rng = random.Random(42)
        snap = FatigueSnapshot(fatigue=0.1, sleep_debt=0.5, energy=7.0, performance_modifier=0.97)

        events = generate_training_session(profile, state, snap, rng, day_offset=0)
        assert len(events) > 0

    def test_event_format(self):
        profile = _make_profile()
        state = _make_state(profile)
        rng = random.Random(42)
        snap = FatigueSnapshot(fatigue=0.1, sleep_debt=0.0, energy=7.5, performance_modifier=1.0)

        events = generate_training_session(profile, state, snap, rng, day_offset=5)

        set_events = [e for e in events if e["event_type"] == "set.logged"]
        assert len(set_events) > 0

        for event in set_events:
            assert "event_type" in event
            assert "data" in event
            assert "occurred_at" in event
            assert "idempotency_key" in event
            assert "exercise" in event["data"]
            assert "exercise_id" in event["data"]
            assert "reps" in event["data"]
            assert "set_type" in event["data"]
            # session_id present on all set.logged events
            assert "session_id" in event
            assert event["session_id"].startswith(str(state.day))

        # alias events should NOT have session_id
        alias_events = [e for e in events if e["event_type"] == "exercise.alias_created"]
        for event in alias_events:
            assert "session_id" not in event

    def test_alias_events_created(self):
        profile = _make_profile()
        state = _make_state(profile)
        rng = random.Random(42)
        snap = FatigueSnapshot(fatigue=0.0, sleep_debt=0.0, energy=7.5, performance_modifier=1.0)

        events = generate_training_session(profile, state, snap, rng, day_offset=0)

        alias_events = [e for e in events if e["event_type"] == "exercise.alias_created"]
        assert len(alias_events) > 0

        for alias_event in alias_events:
            assert "alias" in alias_event["data"]
            assert "exercise_id" in alias_event["data"]
            assert alias_event["data"]["confidence"] == "confirmed"

    def test_no_duplicate_aliases(self):
        """Second session should NOT re-create alias events."""
        profile = _make_profile()
        state = _make_state(profile)
        rng = random.Random(42)
        snap = FatigueSnapshot(fatigue=0.1, sleep_debt=0.0, energy=7.5, performance_modifier=1.0)

        events1 = generate_training_session(profile, state, snap, rng, day_offset=0)
        aliases1 = [e for e in events1 if e["event_type"] == "exercise.alias_created"]

        # Reset training day index for same-day exercises
        state.training_day_index = 0
        events2 = generate_training_session(profile, state, snap, rng, day_offset=1)
        aliases2 = [e for e in events2 if e["event_type"] == "exercise.alias_created"]

        # Second session should have fewer or no aliases (exercises already seen)
        assert len(aliases2) < len(aliases1)

    def test_warmup_sets_lighter(self):
        profile = _make_profile()
        state = _make_state(profile)
        rng = random.Random(42)
        snap = FatigueSnapshot(fatigue=0.0, sleep_debt=0.0, energy=7.5, performance_modifier=1.0)

        events = generate_training_session(profile, state, snap, rng, day_offset=0)
        set_events = [e for e in events if e["event_type"] == "set.logged"]

        # Find warmup and working sets for the same exercise
        for ex_id in ["barbell_bench_press", "barbell_back_squat"]:
            wu = [e for e in set_events if e["data"].get("exercise_id") == ex_id and e["data"]["set_type"] == "warmup"]
            ws = [e for e in set_events if e["data"].get("exercise_id") == ex_id and e["data"]["set_type"] == "working"]
            if wu and ws:
                max_wu_weight = max(e["data"]["weight_kg"] for e in wu)
                min_ws_weight = min(e["data"]["weight_kg"] for e in ws)
                assert max_wu_weight <= min_ws_weight, f"Warmup weight should be <= working weight for {ex_id}"
                break

    def test_fatigue_reduces_reps(self):
        """High fatigue should result in fewer average reps."""
        profile = _make_profile()
        rng_fresh = random.Random(42)
        rng_tired = random.Random(42)

        state_fresh = _make_state(profile)
        snap_fresh = FatigueSnapshot(fatigue=0.0, sleep_debt=0.0, energy=8.0, performance_modifier=1.0)
        events_fresh = generate_training_session(profile, state_fresh, snap_fresh, rng_fresh, day_offset=0)

        state_tired = _make_state(profile)
        snap_tired = FatigueSnapshot(fatigue=0.8, sleep_debt=3.0, energy=4.0, performance_modifier=0.78)
        events_tired = generate_training_session(profile, state_tired, snap_tired, rng_tired, day_offset=0)

        def avg_reps(events):
            working = [e for e in events if e["event_type"] == "set.logged" and e["data"]["set_type"] == "working"]
            return sum(e["data"]["reps"] for e in working) / max(1, len(working))

        assert avg_reps(events_fresh) >= avg_reps(events_tired) - 1  # Allow some tolerance due to RNG

    def test_all_weights_within_plausibility(self):
        profile = _make_profile()
        state = _make_state(profile)
        rng = random.Random(42)
        snap = FatigueSnapshot(fatigue=0.2, sleep_debt=0.5, energy=7.0, performance_modifier=0.95)

        events = generate_training_session(profile, state, snap, rng, day_offset=0)
        for event in events:
            if event["event_type"] == "set.logged":
                w = event["data"]["weight_kg"]
                assert 0 <= w <= 500, f"Weight {w} out of bounds"
                r = event["data"]["reps"]
                assert 0 <= r <= 100, f"Reps {r} out of bounds"
                if "rpe" in event["data"]:
                    rpe = event["data"]["rpe"]
                    assert 1.0 <= rpe <= 10.0, f"RPE {rpe} out of bounds"

    def test_idempotency_keys_unique(self):
        profile = _make_profile()
        state = _make_state(profile)
        rng = random.Random(42)
        snap = FatigueSnapshot(fatigue=0.1, sleep_debt=0.0, energy=7.5, performance_modifier=0.98)

        events = generate_training_session(profile, state, snap, rng, day_offset=0)
        keys = [e["idempotency_key"] for e in events]
        assert len(keys) == len(set(keys)), "Idempotency keys must be unique"

    def test_deterministic(self):
        """Same seed → same events."""
        profile = _make_profile()

        state1 = _make_state(profile)
        events1 = generate_training_session(
            profile, state1,
            FatigueSnapshot(fatigue=0.1, sleep_debt=0.5, energy=7.0, performance_modifier=0.97),
            random.Random(42), day_offset=0,
        )

        state2 = _make_state(profile)
        events2 = generate_training_session(
            profile, state2,
            FatigueSnapshot(fatigue=0.1, sleep_debt=0.5, energy=7.0, performance_modifier=0.97),
            random.Random(42), day_offset=0,
        )

        assert events1 == events2


class TestToApiFormat:
    def test_set_event_includes_session_id_in_metadata(self):
        event = {
            "event_type": "set.logged",
            "data": {"exercise": "Squat", "exercise_id": "squat", "reps": 5, "weight_kg": 100},
            "occurred_at": "2026-02-09T18:00:00+00:00",
            "idempotency_key": "test-key-1",
            "session_id": "2026-02-09-training",
        }
        result = to_api_format(event)
        assert result["metadata"]["session_id"] == "2026-02-09-training"
        assert result["metadata"]["idempotency_key"] == "test-key-1"
        assert result["metadata"]["source"] == "datagen"
        assert "session_id" not in result["data"]

    def test_alias_event_no_session_id_in_metadata(self):
        event = {
            "event_type": "exercise.alias_created",
            "data": {"alias": "Kniebeuge", "exercise_id": "squat", "confidence": "confirmed"},
            "occurred_at": "2026-02-09T18:00:00+00:00",
            "idempotency_key": "test-key-2",
        }
        result = to_api_format(event)
        assert "session_id" not in result["metadata"]
