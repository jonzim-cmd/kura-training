"""Canonical external activity contract v1 (tm5.1).

Provider adapters map raw payloads into this contract before ingestion into core
event types. The schema is provider-agnostic and carries explicit provenance.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

CONTRACT_VERSION_V1 = "external_activity.v1"

REQUIRED_FIELDS_V1: dict[str, tuple[str, ...]] = {
    "root": (
        "contract_version",
        "source",
        "workout",
        "session",
        "provenance",
    ),
    "source": (
        "provider",
        "provider_user_id",
        "external_activity_id",
        "ingestion_method",
    ),
    "workout": ("workout_type",),
    "session": ("started_at",),
    "set": ("sequence", "exercise"),
    "provenance": ("mapping_version", "mapped_at"),
}

OPTIONAL_FIELDS_V1: dict[str, tuple[str, ...]] = {
    "root": ("sets",),
    "source": (
        "external_event_version",
        "raw_payload_ref",
        "imported_at",
    ),
    "workout": (
        "title",
        "sport",
        "duration_seconds",
        "distance_meters",
        "energy_kj",
        "calories_kcal",
        "heart_rate_avg",
        "heart_rate_max",
        "power_watt",
        "pace_min_per_km",
        "session_rpe",
    ),
    "session": (
        "ended_at",
        "timezone",
        "local_date",
        "local_week",
        "session_id",
    ),
    "set": (
        "exercise_id",
        "set_type",
        "reps",
        "weight_kg",
        "duration_seconds",
        "distance_meters",
        "rest_seconds",
        "rpe",
        "rir",
    ),
    "provenance": (
        "source_confidence",
        "field_provenance",
        "unsupported_fields",
        "warnings",
    ),
    "field_provenance": (
        "transform",
        "unit_original",
        "unit_normalized",
        "notes",
    ),
}


def _normalized_non_empty(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


class SourceLayerV1(BaseModel):
    provider: str
    provider_user_id: str
    external_activity_id: str
    external_event_version: str | None = None
    ingestion_method: Literal["file_import", "connector_api", "manual_backfill"]
    raw_payload_ref: str | None = None
    imported_at: datetime | None = None

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        return _normalized_non_empty(value, field_name="provider").lower()

    @field_validator("provider_user_id", "external_activity_id")
    @classmethod
    def validate_non_empty_source_ids(cls, value: str, info: Any) -> str:
        return _normalized_non_empty(value, field_name=info.field_name)

    @field_validator("external_event_version", "raw_payload_ref")
    @classmethod
    def trim_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class WorkoutSliceV1(BaseModel):
    workout_type: str
    title: str | None = None
    sport: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    distance_meters: float | None = Field(default=None, ge=0)
    energy_kj: float | None = Field(default=None, ge=0)
    calories_kcal: float | None = Field(default=None, ge=0)
    heart_rate_avg: float | None = Field(default=None, ge=0)
    heart_rate_max: float | None = Field(default=None, ge=0)
    power_watt: float | None = Field(default=None, ge=0)
    pace_min_per_km: float | None = Field(default=None, ge=0)
    session_rpe: float | None = Field(default=None, ge=0, le=10)

    @field_validator("workout_type")
    @classmethod
    def validate_workout_type(cls, value: str) -> str:
        return _normalized_non_empty(value, field_name="workout_type").lower()

    @field_validator("title", "sport")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class SessionSliceV1(BaseModel):
    started_at: datetime
    ended_at: datetime | None = None
    timezone: str | None = None
    local_date: str | None = None
    local_week: str | None = None
    session_id: str | None = None

    @field_validator("timezone", "local_date", "local_week", "session_id")
    @classmethod
    def normalize_optional_session_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def validate_time_window(self) -> "SessionSliceV1":
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("session.ended_at must be >= session.started_at")
        return self


class SetSliceV1(BaseModel):
    sequence: int = Field(ge=1)
    exercise: str
    exercise_id: str | None = None
    set_type: str | None = None
    reps: int | None = Field(default=None, ge=0)
    weight_kg: float | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    distance_meters: float | None = Field(default=None, ge=0)
    rest_seconds: float | None = Field(default=None, ge=0)
    rpe: float | None = Field(default=None, ge=0, le=10)
    rir: float | None = Field(default=None, ge=0, le=10)

    @field_validator("exercise")
    @classmethod
    def validate_exercise(cls, value: str) -> str:
        return _normalized_non_empty(value, field_name="set.exercise")

    @field_validator("exercise_id", "set_type")
    @classmethod
    def trim_optional_set_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class FieldProvenanceV1(BaseModel):
    source_path: str
    confidence: float = Field(ge=0, le=1)
    status: Literal["mapped", "estimated", "unsupported", "dropped"] = "mapped"
    transform: str | None = None
    unit_original: str | None = None
    unit_normalized: str | None = None
    notes: str | None = None

    @field_validator("source_path")
    @classmethod
    def validate_source_path(cls, value: str) -> str:
        return _normalized_non_empty(value, field_name="field_provenance.source_path")

    @field_validator("transform", "unit_original", "unit_normalized", "notes")
    @classmethod
    def trim_optional_provenance_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ProvenanceLayerV1(BaseModel):
    mapping_version: str
    mapped_at: datetime
    source_confidence: float = Field(default=1.0, ge=0, le=1)
    field_provenance: dict[str, FieldProvenanceV1] = Field(default_factory=dict)
    unsupported_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("mapping_version")
    @classmethod
    def validate_mapping_version(cls, value: str) -> str:
        return _normalized_non_empty(value, field_name="provenance.mapping_version")

    @field_validator("unsupported_fields", "warnings")
    @classmethod
    def normalize_list_entries(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            cleaned = value.strip()
            if cleaned:
                normalized.append(cleaned)
        return normalized


class CanonicalExternalActivityV1(BaseModel):
    contract_version: Literal[CONTRACT_VERSION_V1] = CONTRACT_VERSION_V1
    source: SourceLayerV1
    workout: WorkoutSliceV1
    session: SessionSliceV1
    sets: list[SetSliceV1] = Field(default_factory=list)
    provenance: ProvenanceLayerV1

    @model_validator(mode="after")
    def validate_set_sequences(self) -> "CanonicalExternalActivityV1":
        seen: set[int] = set()
        for set_entry in self.sets:
            if set_entry.sequence in seen:
                raise ValueError(
                    f"set.sequence must be unique per activity (duplicate: {set_entry.sequence})"
                )
            seen.add(set_entry.sequence)
        return self


def validate_external_activity_contract_v1(
    payload: dict[str, Any],
) -> CanonicalExternalActivityV1:
    """Validate payload against canonical external activity contract v1."""
    return CanonicalExternalActivityV1.model_validate(payload)


def contract_field_inventory_v1() -> dict[str, dict[str, tuple[str, ...]]]:
    """Expose required/optional fields for docs and adapter generation."""
    return {
        "required": REQUIRED_FIELDS_V1,
        "optional": OPTIONAL_FIELDS_V1,
    }
