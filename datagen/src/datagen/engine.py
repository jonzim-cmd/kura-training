"""Simulation engine — day-by-day loop orchestrating all generators.

The engine ties together all domain generators and the fatigue model
to produce a realistic, correlated stream of events.
"""

from __future__ import annotations

import random
from datetime import timedelta

from datagen.fatigue import (
    FatigueSnapshot,
    should_trigger_injury,
    update_fatigue_rest_day,
    update_fatigue_training_day,
    update_sleep_debt,
)
from datagen.generators.body_composition import (
    generate_bodyweight,
    generate_measurements,
    update_bodyweight_trend,
)
from datagen.generators.nutrition import generate_nutrition
from datagen.generators.plan import generate_training_plan
from datagen.generators.profile import (
    generate_goal_event,
    generate_injury_event,
    generate_profile_events,
)
from datagen.generators.recovery import generate_energy, generate_sleep, generate_soreness
from datagen.generators.targets import generate_target_events
from datagen.generators.training import generate_training_session, get_session_exercises
from datagen.models import AthleteProfile, AthleteState


class SimulationEngine:
    """Day-by-day simulation engine that generates Kura-compatible events."""

    def __init__(self, profile: AthleteProfile, *, novel_fields: bool = False):
        self.profile = profile
        self.novel_fields = novel_fields
        self.rng = random.Random(profile.seed)

    def run(self, days: int) -> list[dict]:
        """Run the simulation for the given number of days.

        Returns a list of event dicts in chronological order.
        """
        state = AthleteState.from_profile(self.profile)
        events: list[dict] = []

        # Day 0: onboarding events
        events.extend(generate_profile_events(self.profile, state, 0))
        events.extend(generate_goal_event(self.profile, state, 0))
        events.extend(generate_training_plan(self.profile, state, 0))
        events.extend(generate_target_events(self.profile, state, 0))

        # Track yesterday's exercises for soreness generation
        yesterdays_exercises: list[str] | None = None
        injury_triggered = False

        for day_offset in range(days):
            if day_offset > 0:
                state.advance_day()

            is_training = self._is_training_day(state)

            # 1. Sleep (from last night)
            sleep_events = generate_sleep(
                self.profile, state, self.rng, day_offset,
                novel_fields=self.novel_fields,
            )
            events.extend(sleep_events)

            # Update sleep debt from generated sleep
            actual_sleep = sleep_events[0]["data"]["duration_hours"]
            update_sleep_debt(state, self.profile.sleep_avg_hours, actual_sleep)

            # 2. Soreness (DOMS from yesterday's training)
            if yesterdays_exercises:
                events.extend(
                    generate_soreness(
                        self.profile, state, self.rng, day_offset,
                        trained_exercises_yesterday=yesterdays_exercises,
                    )
                )

            # 3. Energy
            events.extend(
                generate_energy(
                    self.profile, state, self.rng, day_offset, is_training,
                    novel_fields=self.novel_fields,
                )
            )

            # 4. Bodyweight (daily)
            events.extend(generate_bodyweight(self.profile, state, self.rng, day_offset))

            # 5. Measurements (periodic)
            events.extend(generate_measurements(self.profile, state, self.rng, day_offset))

            # 6. Training (if scheduled)
            if is_training:
                fatigue_snap = FatigueSnapshot.from_state(state, self.rng.gauss(0, 0.3))
                session_exercises = get_session_exercises(self.profile, state)
                training_events = generate_training_session(
                    self.profile, state, fatigue_snap, self.rng, day_offset,
                    novel_fields=self.novel_fields,
                )
                events.extend(training_events)

                # Update fatigue from training
                total_sets = getattr(state, "_session_total_sets", 0)
                avg_intensity = getattr(state, "_session_avg_intensity", 0.0)
                update_fatigue_training_day(state, total_sets, avg_intensity)

                yesterdays_exercises = session_exercises
            else:
                update_fatigue_rest_day(state)
                yesterdays_exercises = None

                # 6b. Cardio on rest days (orphaned event type — no handler)
                if self.novel_fields:
                    from datagen.generators.novel_fields import generate_cardio

                    events.extend(
                        generate_cardio(self.profile, state, self.rng, day_offset)
                    )

            # 7. Nutrition (every day)
            nutrition_events, total_cals = generate_nutrition(
                self.profile, state, self.rng, day_offset, is_training,
                novel_fields=self.novel_fields,
            )
            events.extend(nutrition_events)

            # 7b. Supplements (orphaned event type — no handler)
            if self.novel_fields:
                from datagen.generators.novel_fields import generate_supplements

                events.extend(
                    generate_supplements(self.profile, state, self.rng, day_offset)
                )

            # 8. Update bodyweight trend from nutrition
            update_bodyweight_trend(state, self.profile.calorie_target, total_cals)

            # 9. Check for injury (after ~6 weeks, max 1 injury per simulation)
            if (
                not injury_triggered
                and day_offset >= 42
                and should_trigger_injury(state, self.rng.random())
            ):
                # Pick affected area from current soreness
                affected = max(state.soreness, key=state.soreness.get) if state.soreness else "lower_back"
                events.extend(generate_injury_event(self.profile, state, day_offset, affected))
                injury_triggered = True

            # 10. Weekly 1RM progression
            if state.day.weekday() == 6:  # Sunday: weekly progression
                self._apply_weekly_progression(state)

        return events

    def _is_training_day(self, state: AthleteState) -> bool:
        """Determine if today is a training day based on the weekly schedule."""
        weekday = state.day.weekday()  # 0=Monday
        freq = self.profile.training_days_per_week

        training_days = {
            3: {0, 2, 4},           # Mon, Wed, Fri
            4: {0, 1, 3, 4},       # Mon, Tue, Thu, Fri
            5: {0, 1, 2, 3, 4},   # Mon-Fri
            6: {0, 1, 2, 3, 4, 5},  # Mon-Sat
        }

        return weekday in training_days.get(freq, {0, 2, 4})

    def _apply_weekly_progression(self, state: AthleteState) -> None:
        """Apply weekly 1RM progression based on experience level."""
        rate = self.profile.progression_rate
        for ex_id in state.current_1rms:
            if state.current_1rms[ex_id] > 0:
                state.current_1rms[ex_id] = round(
                    state.current_1rms[ex_id] * (1 + rate), 1,
                )
