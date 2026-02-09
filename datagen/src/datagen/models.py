"""Core data models for the simulation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass(frozen=True)
class AthleteProfile:
    """Immutable athlete configuration — defines the simulation parameters."""

    name: str
    experience_level: str  # beginner, intermediate, advanced
    bodyweight_kg: float
    training_days_per_week: int  # 3-6
    exercises: list[str]  # canonical exercise_ids
    squat_1rm_kg: float
    bench_1rm_kg: float
    deadlift_1rm_kg: float
    ohp_1rm_kg: float
    sleep_avg_hours: float
    sleep_std_hours: float
    calorie_target: int
    protein_target_g: int
    progression_rate: float  # weekly 1RM increase factor (e.g. 0.015 = +1.5%/week)
    start_date: date = field(default_factory=lambda: date(2025, 10, 1))
    seed: int = 42

    @property
    def starting_1rms(self) -> dict[str, float]:
        """Map of exercise_id → starting estimated 1RM."""
        from datagen.exercises import EXERCISES

        result: dict[str, float] = {}
        for ex_id in self.exercises:
            ex = EXERCISES.get(ex_id)
            if ex is None:
                continue
            if ex_id == "barbell_back_squat":
                result[ex_id] = self.squat_1rm_kg
            elif ex_id == "barbell_bench_press":
                result[ex_id] = self.bench_1rm_kg
            elif ex_id == "conventional_deadlift":
                result[ex_id] = self.deadlift_1rm_kg
            elif ex_id == "overhead_press":
                result[ex_id] = self.ohp_1rm_kg
            else:
                # Derive from squat 1RM using the exercise's ratio
                result[ex_id] = round(self.squat_1rm_kg * ex.rm_ratio, 1)
        return result


@dataclass
class AthleteState:
    """Mutable simulation state — changes day by day."""

    day: date
    fatigue: float  # 0.0-1.0
    current_1rms: dict[str, float]  # exercise_id → current estimated 1RM
    bodyweight_kg: float
    sleep_debt: float  # accumulated sleep deficit
    energy_baseline: float  # long-term energy trend (1-10)
    soreness: dict[str, float]  # muscle_group → severity (decays daily)
    mesocycle_week: int  # 1-4 within current mesocycle
    mesocycle_day: int  # day within current mesocycle (0-27)
    weekly_volume: dict[str, int]  # exercise_id → sets this week
    training_day_index: int  # which training day this week (0-based)
    aliases_created: set[str]  # exercise_ids that already have alias events
    total_days: int  # days elapsed since simulation start

    @classmethod
    def from_profile(cls, profile: AthleteProfile) -> AthleteState:
        return cls(
            day=profile.start_date,
            fatigue=0.0,
            current_1rms=dict(profile.starting_1rms),
            bodyweight_kg=profile.bodyweight_kg,
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

    def advance_day(self) -> None:
        """Move to the next day, updating mesocycle tracking."""
        self.day += timedelta(days=1)
        self.total_days += 1
        self.mesocycle_day += 1

        # Mesocycle is 4 weeks = 28 days
        if self.mesocycle_day >= 28:
            self.mesocycle_day = 0
            self.mesocycle_week = 1
        else:
            self.mesocycle_week = (self.mesocycle_day // 7) + 1

        # Reset weekly volume on Monday
        if self.day.weekday() == 0:
            self.weekly_volume = {}
            self.training_day_index = 0

        # Decay soreness (each area loses ~40% per day)
        decayed = {}
        for area, severity in self.soreness.items():
            new_severity = severity * 0.6
            if new_severity >= 0.5:
                decayed[area] = new_severity
        self.soreness = decayed
