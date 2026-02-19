"""Unified training session contract for modality-neutral logging (session.logged v1).

This module defines a single block-based payload that supports strength,
endurance, sprint, plyometrics, and mixed sessions without forcing sensor data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

CONTRACT_VERSION_V1 = "session.logged.v1"

MEASUREMENT_STATES: tuple[str, ...] = (
    "measured",
    "estimated",
    "inferred",
    "not_measured",
    "not_applicable",
)

PROVENANCE_SOURCE_TYPES: tuple[str, ...] = (
    "manual",
    "imported",
    "inferred",
    "corrected",
)

BLOCK_TYPES: tuple[str, ...] = (
    "strength_set",
    "explosive_power",
    "plyometric_reactive",
    "sprint_accel_maxv",
    "speed_endurance",
    "interval_endurance",
    "continuous_endurance",
    "tempo_threshold",
    "circuit_hybrid",
    "technique_coordination",
    "recovery_session",
)

PERFORMANCE_BLOCK_TYPES: tuple[str, ...] = tuple(
    block_type for block_type in BLOCK_TYPES if block_type != "recovery_session"
)

RELATIVE_INTENSITY_REFERENCE_TYPES: tuple[str, ...] = (
    "e1rm",
    "one_rm",
    "mss",
    "critical_speed",
    "critical_power",
    "mas",
    "vvo2max",
    "asr",
    "jump_height",
    "custom",
)

ERROR_TYPE_INVALID_MEASUREMENT_STATE = "session_logged_invalid_measurement_state"
ERROR_TYPE_MEASUREMENT_VALUE_OR_REFERENCE_REQUIRED = (
    "session_logged_measurement_value_or_reference_required"
)
ERROR_TYPE_DOSE_WORK_DIMENSION_MISSING = "session_logged_dose_work_dimension_missing"
ERROR_TYPE_INTENSITY_STATUS_PROVIDED_WITHOUT_ANCHOR = (
    "session_logged_intensity_status_provided_without_anchor"
)
ERROR_TYPE_INTENSITY_STATUS_NOT_APPLICABLE_WITH_ANCHOR = (
    "session_logged_intensity_status_not_applicable_with_anchor"
)
ERROR_TYPE_PERFORMANCE_BLOCK_MISSING_ANCHOR = (
    "session_logged_performance_block_missing_anchor"
)
ERROR_TYPE_SESSION_META_TEMPORAL_ORDER_INVALID = (
    "session_logged_session_meta_temporal_order_invalid"
)


def _normalize_non_empty(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


class MeasurementValueV1(BaseModel):
    value: Any | None = None
    unit: str | None = None
    reference: str | None = None
    measurement_state: str

    @field_validator("measurement_state")
    @classmethod
    def validate_measurement_state(cls, value: str) -> str:
        normalized = _normalize_non_empty(value, field_name="measurement_state").lower()
        if normalized not in MEASUREMENT_STATES:
            allowed = ", ".join(MEASUREMENT_STATES)
            raise PydanticCustomError(
                ERROR_TYPE_INVALID_MEASUREMENT_STATE,
                f"measurement_state must be one of: {allowed}",
            )
        return normalized

    @field_validator("unit", "reference")
    @classmethod
    def normalize_optional_fields(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_state_value_pair(self) -> "MeasurementValueV1":
        if self.measurement_state in {"measured", "estimated", "inferred"}:
            if self.value is None and self.reference is None:
                raise PydanticCustomError(
                    ERROR_TYPE_MEASUREMENT_VALUE_OR_REFERENCE_REQUIRED,
                    "measurement_state requires value or reference when state is measured/estimated/inferred"
                )
        return self


class DoseSliceV1(BaseModel):
    duration_seconds: float | None = Field(default=None, ge=0)
    distance_meters: float | None = Field(default=None, ge=0)
    reps: int | None = Field(default=None, ge=0)
    contacts: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_contains_work_dimension(self) -> "DoseSliceV1":
        has_dimension = any(
            value is not None
            for value in (
                self.duration_seconds,
                self.distance_meters,
                self.reps,
                self.contacts,
            )
        )
        if not has_dimension:
            raise PydanticCustomError(
                ERROR_TYPE_DOSE_WORK_DIMENSION_MISSING,
                "dose slice must define at least one work dimension (duration_seconds, distance_meters, reps, contacts)"
            )
        return self


class BlockDoseV1(BaseModel):
    work: DoseSliceV1
    recovery: DoseSliceV1 | None = None
    repeats: int | None = Field(default=None, ge=1)


class SessionProvenanceV1(BaseModel):
    source_type: str
    source_ref: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, value: str) -> str:
        normalized = _normalize_non_empty(value, field_name="source_type").lower()
        if normalized not in PROVENANCE_SOURCE_TYPES:
            allowed = ", ".join(PROVENANCE_SOURCE_TYPES)
            raise ValueError(f"source_type must be one of: {allowed}")
        return normalized

    @field_validator("source_ref")
    @classmethod
    def normalize_optional_source_ref(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)


class RelativeIntensityV1(BaseModel):
    value_pct: float = Field(gt=0, le=130)
    reference_type: str
    reference_value: float | None = Field(default=None, gt=0)
    reference_measured_at: datetime | None = None
    reference_confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("reference_type")
    @classmethod
    def validate_reference_type(cls, value: str) -> str:
        normalized = _normalize_non_empty(value, field_name="reference_type").lower()
        if normalized not in RELATIVE_INTENSITY_REFERENCE_TYPES:
            allowed = ", ".join(RELATIVE_INTENSITY_REFERENCE_TYPES)
            raise ValueError(f"reference_type must be one of: {allowed}")
        return normalized


class SessionBlockV1(BaseModel):
    block_type: str
    capability_target: str | None = None
    dose: BlockDoseV1
    recovery_mode: str | None = None
    intensity_anchors_status: Literal["provided", "not_applicable"] | None = None
    intensity_anchors: list[MeasurementValueV1] = Field(default_factory=list)
    relative_intensity: RelativeIntensityV1 | None = None
    metrics: dict[str, MeasurementValueV1] = Field(default_factory=dict)
    subjective_response: dict[str, MeasurementValueV1] = Field(default_factory=dict)
    provenance: SessionProvenanceV1 | None = None

    @field_validator("block_type")
    @classmethod
    def validate_block_type(cls, value: str) -> str:
        normalized = _normalize_non_empty(value, field_name="block_type").lower()
        if normalized not in BLOCK_TYPES:
            allowed = ", ".join(BLOCK_TYPES)
            raise ValueError(f"block_type must be one of: {allowed}")
        return normalized

    @field_validator("capability_target", "recovery_mode")
    @classmethod
    def normalize_optional_block_text(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_intensity_anchor_policy(self) -> "SessionBlockV1":
        if self.intensity_anchors_status == "provided" and not self.intensity_anchors:
            raise PydanticCustomError(
                ERROR_TYPE_INTENSITY_STATUS_PROVIDED_WITHOUT_ANCHOR,
                "intensity_anchors_status='provided' requires at least one intensity anchor"
            )
        if self.intensity_anchors_status == "not_applicable" and self.intensity_anchors:
            raise PydanticCustomError(
                ERROR_TYPE_INTENSITY_STATUS_NOT_APPLICABLE_WITH_ANCHOR,
                "intensity_anchors_status='not_applicable' requires intensity_anchors to be empty"
            )

        if self.block_type in PERFORMANCE_BLOCK_TYPES:
            has_anchor = bool(self.intensity_anchors)
            explicitly_not_applicable = self.intensity_anchors_status == "not_applicable"
            if not has_anchor and not explicitly_not_applicable:
                raise PydanticCustomError(
                    ERROR_TYPE_PERFORMANCE_BLOCK_MISSING_ANCHOR,
                    (
                        "performance block missing required intensity signal "
                        "(anchor or explicit not_applicable)"
                    ),
                )

        return self


class SessionMetaV1(BaseModel):
    sport: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    timezone: str | None = None
    session_id: str | None = None
    notes: str | None = None

    @field_validator("sport", "timezone", "session_id", "notes")
    @classmethod
    def normalize_optional_meta_text(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_temporal_order(self) -> "SessionMetaV1":
        if self.started_at is not None and self.ended_at is not None:
            if self.ended_at < self.started_at:
                raise PydanticCustomError(
                    ERROR_TYPE_SESSION_META_TEMPORAL_ORDER_INVALID,
                    "session_meta.ended_at must be >= session_meta.started_at",
                )
        return self


class SessionLoggedV1(BaseModel):
    contract_version: Literal[CONTRACT_VERSION_V1] = CONTRACT_VERSION_V1
    session_meta: SessionMetaV1
    blocks: list[SessionBlockV1] = Field(min_length=1)
    subjective_response: dict[str, MeasurementValueV1] = Field(default_factory=dict)
    provenance: SessionProvenanceV1 | None = None


def validate_session_logged_payload(payload: dict[str, Any]) -> SessionLoggedV1:
    """Validate payload against the unified session.logged v1 contract."""
    return SessionLoggedV1.model_validate(payload)


def block_catalog_v1() -> dict[str, Any]:
    """Expose contract catalog for system and architecture contract checks."""
    return {
        "contract_version": CONTRACT_VERSION_V1,
        "block_types": list(BLOCK_TYPES),
        "performance_block_types": list(PERFORMANCE_BLOCK_TYPES),
        "measurement_state_values": list(MEASUREMENT_STATES),
        "provenance_source_types": list(PROVENANCE_SOURCE_TYPES),
        "relative_intensity_reference_types": list(RELATIVE_INTENSITY_REFERENCE_TYPES),
        "intensity_policy": {
            "performance_default": "requires_anchor",
            "explicit_not_applicable_key": "intensity_anchors_status",
            "explicit_not_applicable_value": "not_applicable",
            "global_hr_requirement": False,
            "relative_intensity": (
                "optional, used as objective % of reference signal "
                "(e.g. e1rm, mss, critical_speed); when stale/missing, fallback uncertainty increases"
            ),
        },
        "validation_error_types": [
            ERROR_TYPE_INVALID_MEASUREMENT_STATE,
            ERROR_TYPE_MEASUREMENT_VALUE_OR_REFERENCE_REQUIRED,
            ERROR_TYPE_DOSE_WORK_DIMENSION_MISSING,
            ERROR_TYPE_INTENSITY_STATUS_PROVIDED_WITHOUT_ANCHOR,
            ERROR_TYPE_INTENSITY_STATUS_NOT_APPLICABLE_WITH_ANCHOR,
            ERROR_TYPE_PERFORMANCE_BLOCK_MISSING_ANCHOR,
            ERROR_TYPE_SESSION_META_TEMPORAL_ORDER_INVALID,
        ],
    }
