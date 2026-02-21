"""Canonical event-type sets for inference handlers and replay paths."""

from __future__ import annotations

READINESS_SIGNAL_EVENT_TYPES: tuple[str, ...] = (
    "set.logged",
    "session.logged",
    "set.corrected",
    "sleep.logged",
    "soreness.logged",
    "energy.logged",
    "recovery.daily_checkin",
    "external.activity_imported",
)

CAUSAL_SIGNAL_EVENT_TYPES: tuple[str, ...] = (
    "program.started",
    "training_plan.created",
    "training_plan.updated",
    "training_plan.archived",
    "goal.set",
    "objective.set",
    "objective.updated",
    "objective.archived",
    "advisory.override.recorded",
    "meal.logged",
    "nutrition_target.set",
    "supplement.regimen.set",
    "supplement.regimen.paused",
    "supplement.regimen.resumed",
    "supplement.regimen.stopped",
    "supplement.taken",
    "supplement.skipped",
    "supplement.logged",
    "sleep.logged",
    "sleep_target.set",
    "set.logged",
    "session.logged",
    "set.corrected",
    "energy.logged",
    "soreness.logged",
    "recovery.daily_checkin",
    "exercise.alias_created",
    "external.activity_imported",
)

NIGHTLY_REFIT_TRIGGER_EVENT_TYPES: tuple[str, ...] = (
    "set.logged",
    "session.logged",
    "set.corrected",
    "exercise.alias_created",
    "goal.set",
    "objective.set",
    "objective.updated",
    "objective.archived",
    "advisory.override.recorded",
    "sleep.logged",
    "soreness.logged",
    "energy.logged",
    "recovery.daily_checkin",
    "supplement.regimen.set",
    "supplement.regimen.paused",
    "supplement.regimen.resumed",
    "supplement.regimen.stopped",
    "supplement.taken",
    "supplement.skipped",
    "supplement.logged",
    "external.activity_imported",
)

CAPABILITY_BACKFILL_TRIGGER_EVENT_TYPES: tuple[str, ...] = (
    "set.logged",
    "session.logged",
    "set.corrected",
    "external.activity_imported",
)

OBJECTIVE_BACKFILL_TRIGGER_EVENT_TYPES: tuple[str, ...] = (
    "goal.set",
    "objective.set",
    "objective.updated",
    "objective.archived",
    "advisory.override.recorded",
    "profile.updated",
    "set.logged",
    "session.logged",
    "external.activity_imported",
)

EVAL_READINESS_EVENT_TYPES: tuple[str, ...] = (
    *READINESS_SIGNAL_EVENT_TYPES,
    "preference.set",
    "event.retracted",
)

EVAL_CAUSAL_EVENT_TYPES: tuple[str, ...] = (
    *CAUSAL_SIGNAL_EVENT_TYPES,
    "preference.set",
    "event.retracted",
)

EVAL_SEMANTIC_EVENT_TYPES: tuple[str, ...] = (
    "set.logged",
    "exercise.alias_created",
    "event.retracted",
)

EVAL_STRENGTH_EVENT_TYPES: tuple[str, ...] = (
    *EVAL_SEMANTIC_EVENT_TYPES,
)

EVAL_CAPABILITY_EVENT_TYPES: tuple[str, ...] = (
    "set.logged",
    "session.logged",
    "set.corrected",
    "external.activity_imported",
    "preference.set",
    "event.retracted",
)
