"""Training event generator — set.logged + exercise.alias_created events.

Generates realistic training sessions with:
- RPE-based weight selection via Tuchscherer table
- Warmup sets at progressive percentages
- Fatigue-modulated rep counts
- Mesocycle periodization (volume waves, deload weeks)
- Alias creation on first use of each exercise
"""

from __future__ import annotations

import random
from datetime import datetime, time, timezone

from datagen.exercises import EXERCISES, Exercise
from datagen.fatigue import FatigueSnapshot
from datagen.models import AthleteProfile, AthleteState
from datagen.periodization import (
    percentage_to_weight,
    rpe_to_percentage,
    target_rpe_for_week,
    warmup_percentages,
    working_sets_for_week,
)

# Training split templates by frequency
# Each entry maps training_day_index → list of exercise_ids
SPLIT_TEMPLATES: dict[int, list[list[str]]] = {
    3: [
        # Full body 3x/week
        ["barbell_back_squat", "barbell_bench_press", "barbell_row", "lateral_raise"],
        ["conventional_deadlift", "overhead_press", "pull_up", "barbell_curl"],
        ["barbell_back_squat", "barbell_bench_press", "romanian_deadlift", "tricep_pushdown"],
    ],
    4: [
        # Upper/Lower 4x/week
        ["barbell_bench_press", "overhead_press", "barbell_row", "lateral_raise", "tricep_pushdown"],
        ["barbell_back_squat", "romanian_deadlift", "leg_press", "barbell_curl"],
        ["overhead_press", "barbell_bench_press", "pull_up", "lateral_raise", "barbell_curl"],
        ["conventional_deadlift", "barbell_back_squat", "leg_press", "tricep_pushdown"],
    ],
    5: [
        # Push/Pull/Legs/Upper/Lower
        ["barbell_bench_press", "overhead_press", "dip", "lateral_raise", "tricep_pushdown"],
        ["conventional_deadlift", "barbell_row", "pull_up", "barbell_curl"],
        ["barbell_back_squat", "leg_press", "romanian_deadlift"],
        ["overhead_press", "barbell_bench_press", "barbell_row", "lateral_raise"],
        ["barbell_back_squat", "romanian_deadlift", "leg_press", "barbell_curl", "tricep_pushdown"],
    ],
    6: [
        # PPL 2x
        ["barbell_bench_press", "overhead_press", "dip", "lateral_raise", "tricep_pushdown"],
        ["conventional_deadlift", "barbell_row", "pull_up", "barbell_curl"],
        ["barbell_back_squat", "leg_press", "romanian_deadlift"],
        ["overhead_press", "barbell_bench_press", "dip", "lateral_raise", "tricep_pushdown"],
        ["barbell_row", "pull_up", "barbell_curl", "romanian_deadlift"],
        ["barbell_back_squat", "leg_press", "conventional_deadlift"],
    ],
}


def get_session_exercises(profile: AthleteProfile, state: AthleteState) -> list[str]:
    """Get exercises for today's training session."""
    freq = profile.training_days_per_week
    template = SPLIT_TEMPLATES.get(freq, SPLIT_TEMPLATES[4])
    day_idx = state.training_day_index % len(template)
    # Filter to exercises the profile actually uses
    session = [ex for ex in template[day_idx] if ex in profile.exercises]
    return session


def generate_training_session(
    profile: AthleteProfile,
    state: AthleteState,
    fatigue_snap: FatigueSnapshot,
    rng: random.Random,
    day_offset: int,
) -> list[dict]:
    """Generate all events for a single training session.

    Returns a list of event dicts (set.logged + exercise.alias_created).
    """
    exercises = get_session_exercises(profile, state)
    if not exercises:
        return []

    events: list[dict] = []
    # Session starts between 17:00-19:00 (evening training)
    hour = rng.randint(17, 19)
    minute = rng.choice([0, 15, 30, 45])
    session_time = datetime.combine(state.day, time(hour, minute), tzinfo=timezone.utc)

    set_counter = 0
    total_sets = 0
    total_intensity = 0.0

    for ex_idx, exercise_id in enumerate(exercises):
        ex = EXERCISES.get(exercise_id)
        if ex is None:
            continue

        # Generate alias event on first use
        alias_events = _maybe_create_alias(profile, state, ex, session_time, day_offset)
        events.extend(alias_events)

        one_rm = state.current_1rms.get(exercise_id, 0.0)
        is_bodyweight = ex.rm_ratio == 0.0

        # Determine rep target based on exercise type and mesocycle week
        target_rpe = target_rpe_for_week(state.mesocycle_week)
        rep_min, rep_max = ex.typical_rep_range
        target_reps = rng.randint(rep_min, rep_max)

        if is_bodyweight:
            # Bodyweight exercises: reps only, no external weight
            events.extend(
                _generate_bodyweight_sets(
                    profile, state, ex, fatigue_snap, rng,
                    session_time, day_offset, set_counter, target_reps, target_rpe,
                )
            )
            n_working = working_sets_for_week(state.mesocycle_week)
            set_counter += n_working + len(warmup_percentages())
            total_sets += n_working
        else:
            # Weighted exercise: warmup + working sets
            working_pct = rpe_to_percentage(target_reps, target_rpe)
            working_weight = percentage_to_weight(one_rm, working_pct)

            # Warmup sets
            for wu_pct in warmup_percentages():
                wu_weight = percentage_to_weight(one_rm, working_pct * wu_pct)
                wu_reps = min(target_reps + 3, 10)  # warmups are slightly higher rep
                events.append(
                    _set_event(
                        profile, state, ex, wu_weight, wu_reps, None,
                        "warmup", session_time, day_offset, set_counter,
                    )
                )
                set_counter += 1

            # Working sets
            n_working = working_sets_for_week(state.mesocycle_week)
            for set_idx in range(n_working):
                # Weight may ramp up slightly across sets
                weight_adj = 1.0
                if set_idx > 0 and rng.random() < 0.3:
                    weight_adj = rng.choice([0.975, 1.0, 1.025])

                actual_weight = percentage_to_weight(one_rm, working_pct * weight_adj)

                # Reps affected by fatigue
                perf = fatigue_snap.performance_modifier
                fatigue_reps = max(1, round(target_reps * perf + rng.gauss(0, 0.5)))
                actual_reps = max(1, min(fatigue_reps, target_reps + 2))

                # Actual RPE: higher when fatigued, increases across sets
                rpe_noise = rng.gauss(0, 0.3)
                actual_rpe = target_rpe + (1.0 - perf) * 3 + set_idx * 0.3 + rpe_noise
                actual_rpe = round(max(6.0, min(10.0, actual_rpe)), 1)

                events.append(
                    _set_event(
                        profile, state, ex, actual_weight, actual_reps,
                        actual_rpe, "working", session_time, day_offset, set_counter,
                    )
                )
                set_counter += 1
                total_sets += 1
                total_intensity += working_pct * weight_adj

        # Track weekly volume
        n_working = working_sets_for_week(state.mesocycle_week)
        state.weekly_volume[exercise_id] = state.weekly_volume.get(exercise_id, 0) + n_working

    # Return events and track session stats for fatigue update
    avg_intensity = total_intensity / max(1, total_sets)
    # Store on state for fatigue update (engine will read these)
    state._session_total_sets = total_sets  # type: ignore[attr-defined]
    state._session_avg_intensity = avg_intensity  # type: ignore[attr-defined]

    state.training_day_index += 1
    return events


def _maybe_create_alias(
    profile: AthleteProfile,
    state: AthleteState,
    exercise: Exercise,
    session_time: datetime,
    day_offset: int,
) -> list[dict]:
    """Create exercise.alias_created event on first use."""
    if exercise.exercise_id in state.aliases_created:
        return []

    state.aliases_created.add(exercise.exercise_id)
    return [
        {
            "event_type": "exercise.alias_created",
            "data": {
                "alias": exercise.alias_de,
                "exercise_id": exercise.exercise_id,
                "confidence": "confirmed",
            },
            "occurred_at": session_time.isoformat(),
            "idempotency_key": f"datagen-{profile.name}-d{day_offset}-alias-{exercise.exercise_id}",
        }
    ]


def _set_event(
    profile: AthleteProfile,
    state: AthleteState,
    exercise: Exercise,
    weight_kg: float,
    reps: int,
    rpe: float | None,
    set_type: str,
    session_time: datetime,
    day_offset: int,
    set_index: int,
) -> dict:
    """Create a single set.logged event."""
    data: dict = {
        "exercise": exercise.alias_de,
        "exercise_id": exercise.exercise_id,
        "weight_kg": weight_kg,
        "reps": reps,
        "set_type": set_type,
    }
    if rpe is not None:
        data["rpe"] = rpe

    return {
        "event_type": "set.logged",
        "data": data,
        "occurred_at": session_time.isoformat(),
        "idempotency_key": f"datagen-{profile.name}-d{day_offset}-set-{set_index}",
    }


def _generate_bodyweight_sets(
    profile: AthleteProfile,
    state: AthleteState,
    exercise: Exercise,
    fatigue_snap: FatigueSnapshot,
    rng: random.Random,
    session_time: datetime,
    day_offset: int,
    set_counter: int,
    target_reps: int,
    target_rpe: float,
) -> list[dict]:
    """Generate sets for bodyweight exercises (pull-ups, dips)."""
    events: list[dict] = []
    n_working = working_sets_for_week(state.mesocycle_week)

    # One lighter warmup set
    wu_reps = max(3, target_reps // 2)
    events.append(
        _set_event(
            profile, state, exercise, 0, wu_reps, None,
            "warmup", session_time, day_offset, set_counter,
        )
    )
    set_counter += 1

    for set_idx in range(n_working):
        perf = fatigue_snap.performance_modifier
        actual_reps = max(1, round(target_reps * perf + rng.gauss(0, 1.0)))
        actual_reps = max(1, min(actual_reps, target_reps + 3))

        rpe_noise = rng.gauss(0, 0.3)
        actual_rpe = target_rpe + (1.0 - perf) * 3 + set_idx * 0.4 + rpe_noise
        actual_rpe = round(max(6.0, min(10.0, actual_rpe)), 1)

        events.append(
            _set_event(
                profile, state, exercise, 0, actual_reps,
                actual_rpe, "working", session_time, day_offset, set_counter,
            )
        )
        set_counter += 1

    return events
