"""Projection rule models — Pydantic validation for agent-created rules.

Phase 3 of the Adaptive Projection System (Decision 10). The agent creates
declarative rules via projection_rule.created events. These models validate
the rule structure before the system builds projections from them.

Two rule patterns:
- field_tracking: Extract numeric fields from known event types → time series
- categorized_tracking: Group orphaned events by a category field
"""

from typing import Any, Literal, Union

from pydantic import BaseModel, field_validator, model_validator


class FieldTrackingRule(BaseModel):
    """Extract numeric fields from known event types as time series.

    Example: hrv_rmssd + deep_sleep_pct from sleep.logged → weekly averages, trends.
    """

    name: str
    type: Literal["field_tracking"]
    source_events: list[str]
    fields: list[str]

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v

    @field_validator("source_events")
    @classmethod
    def source_events_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("source_events must not be empty")
        return v

    @field_validator("fields")
    @classmethod
    def fields_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("fields must not be empty")
        return v


class CategorizedTrackingRule(BaseModel):
    """Group orphaned events by a category field.

    Example: supplement.logged grouped by "name" → per-supplement frequency/trends.
    """

    name: str
    type: Literal["categorized_tracking"]
    source_events: list[str]
    fields: list[str]
    group_by: str

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v

    @field_validator("source_events")
    @classmethod
    def source_events_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("source_events must not be empty")
        return v

    @field_validator("fields")
    @classmethod
    def fields_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("fields must not be empty")
        return v

    @model_validator(mode="after")
    def group_by_in_fields(self) -> "CategorizedTrackingRule":
        if self.group_by not in self.fields:
            raise ValueError(f"group_by '{self.group_by}' must be one of the declared fields")
        return self


ProjectionRule = Union[FieldTrackingRule, CategorizedTrackingRule]


def validate_rule(data: dict[str, Any]) -> ProjectionRule:
    """Validate and parse a rule dict into a typed model.

    Raises pydantic.ValidationError on invalid input.
    """
    rule_type = data.get("type")
    if rule_type == "field_tracking":
        return FieldTrackingRule.model_validate(data)
    elif rule_type == "categorized_tracking":
        return CategorizedTrackingRule.model_validate(data)
    else:
        raise ValueError(f"Unknown rule type: {rule_type!r}. Expected 'field_tracking' or 'categorized_tracking'.")
