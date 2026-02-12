"""Provider adapter interface and ingestion envelope (tm5.2)."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError, field_validator

from .external_activity_contract import (
    CanonicalExternalActivityV1,
    CONTRACT_VERSION_V1,
    validate_external_activity_contract_v1,
)
from .external_identity import (
    activity_payload_fingerprint,
    build_external_idempotency_key,
    source_identity_key,
)

ENVELOPE_VERSION_V1 = "external_ingestion_envelope.v1"

IngestionMethod = Literal["file_import", "connector_api", "manual_backfill"]


def _stable_hash(value: str, size: int = 20) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return digest[:size]


class ValidationIssue(BaseModel):
    code: str
    field: str | None = None
    message: str
    docs_hint: str | None = None

    @field_validator("code", "message")
    @classmethod
    def validate_required_strings(cls, value: str, info: Any) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} must not be empty")
        return cleaned

    @field_validator("field", "docs_hint")
    @classmethod
    def trim_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ValidationReport(BaseModel):
    valid: bool
    errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)


class UnitConversion(BaseModel):
    field: str
    source_unit: str
    target_unit: str
    formula: str

    @field_validator("field", "source_unit", "target_unit", "formula")
    @classmethod
    def non_empty_fields(cls, value: str, info: Any) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} must not be empty")
        return cleaned


class MappingMetadata(BaseModel):
    mapping_version: str
    mapped_fields: list[str] = Field(default_factory=list)
    dropped_fields: list[str] = Field(default_factory=list)
    unit_conversions: list[UnitConversion] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator("mapping_version")
    @classmethod
    def validate_mapping_version(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("mapping_version must not be empty")
        return cleaned

    @field_validator("mapped_fields", "dropped_fields", "notes")
    @classmethod
    def normalize_string_lists(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            cleaned = value.strip()
            if cleaned:
                normalized.append(cleaned)
        return normalized


class IngestionEnvelopeV1(BaseModel):
    envelope_version: Literal[ENVELOPE_VERSION_V1] = ENVELOPE_VERSION_V1
    provider: str
    raw_payload_ref: str | None = None
    raw_payload_hash: str
    canonical_draft: dict[str, Any]
    canonical_activity: CanonicalExternalActivityV1 | None = None
    mapping_metadata: MappingMetadata
    validation_report: ValidationReport
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))

    @field_validator("provider", "raw_payload_hash")
    @classmethod
    def validate_required_fields(cls, value: str, info: Any) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} must not be empty")
        return cleaned

    @field_validator("raw_payload_ref")
    @classmethod
    def normalize_optional_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ExternalProviderAdapter(Protocol):
    """Stable provider adapter interface."""

    provider: str
    mapping_version: str

    def adapt(
        self,
        *,
        provider_user_id: str,
        raw_payload: dict[str, Any],
        raw_payload_ref: str | None = None,
        ingestion_method: IngestionMethod = "file_import",
    ) -> IngestionEnvelopeV1: ...


@dataclass(frozen=True)
class PreparedIngestion:
    provider: str
    source_identity_key: str
    payload_fingerprint: str
    idempotency_key: str
    canonical_activity: CanonicalExternalActivityV1
    warnings: list[str]


class BaseExternalProviderAdapter(ABC):
    """Base adapter with envelope assembly and contract validation."""

    provider: str
    mapping_version: str = "external-mapping-v1"

    def adapt(
        self,
        *,
        provider_user_id: str,
        raw_payload: dict[str, Any],
        raw_payload_ref: str | None = None,
        ingestion_method: IngestionMethod = "file_import",
    ) -> IngestionEnvelopeV1:
        if not provider_user_id.strip():
            raise ValueError("provider_user_id must not be empty")

        canonical_draft = self.map_payload_to_canonical(
            provider_user_id=provider_user_id.strip(),
            raw_payload=raw_payload,
            ingestion_method=ingestion_method,
        )
        raw_hash = _stable_hash(
            json.dumps(raw_payload, sort_keys=True, separators=(",", ":"), default=str)
        )

        try:
            canonical_activity = validate_external_activity_contract_v1(canonical_draft)
            validation_report = ValidationReport(valid=True)
        except ValidationError as exc:
            canonical_activity = None
            validation_report = ValidationReport(
                valid=False,
                errors=[
                    ValidationIssue(
                        code="canonical_contract_validation_failed",
                        field=".".join(str(part) for part in err.get("loc", [])) or None,
                        message=err.get("msg", "invalid payload"),
                        docs_hint=(
                            "Provide required source/session/workout/provenance fields "
                            "and valid numeric ranges."
                        ),
                    )
                    for err in exc.errors()
                ],
            )

        return IngestionEnvelopeV1(
            provider=self.provider,
            raw_payload_ref=raw_payload_ref,
            raw_payload_hash=raw_hash,
            canonical_draft=canonical_draft,
            canonical_activity=canonical_activity,
            mapping_metadata=self.build_mapping_metadata(raw_payload, canonical_draft),
            validation_report=validation_report,
        )

    @abstractmethod
    def map_payload_to_canonical(
        self,
        *,
        provider_user_id: str,
        raw_payload: dict[str, Any],
        ingestion_method: IngestionMethod,
    ) -> dict[str, Any]:
        """Map provider payload to canonical contract draft."""

    def build_mapping_metadata(
        self,
        raw_payload: dict[str, Any],
        canonical_draft: dict[str, Any],
    ) -> MappingMetadata:
        mapped_fields = sorted(
            canonical_draft.keys()
        )
        dropped_fields = sorted(
            key for key in raw_payload.keys() if key not in {"workout", "session", "sets"}
        )
        return MappingMetadata(
            mapping_version=self.mapping_version,
            mapped_fields=mapped_fields,
            dropped_fields=dropped_fields,
        )


class DummyExternalAdapter(BaseExternalProviderAdapter):
    """Test adapter: maps pre-normalized payload into canonical draft."""

    def __init__(self, provider: str, mapping_version: str = "dummy-v1") -> None:
        self.provider = provider.strip().lower()
        self.mapping_version = mapping_version

    def map_payload_to_canonical(
        self,
        *,
        provider_user_id: str,
        raw_payload: dict[str, Any],
        ingestion_method: IngestionMethod,
    ) -> dict[str, Any]:
        external_activity_id = str(raw_payload.get("external_activity_id", "")).strip()
        external_event_version = raw_payload.get("external_event_version")

        return {
            "contract_version": CONTRACT_VERSION_V1,
            "source": {
                "provider": self.provider,
                "provider_user_id": provider_user_id,
                "external_activity_id": external_activity_id,
                "external_event_version": external_event_version,
                "ingestion_method": ingestion_method,
                "raw_payload_ref": raw_payload.get("raw_payload_ref"),
            },
            "workout": raw_payload.get("workout", {}),
            "session": raw_payload.get("session", {}),
            "sets": raw_payload.get("sets", []),
            "provenance": {
                "mapping_version": self.mapping_version,
                "mapped_at": datetime.now(tz=UTC).isoformat(),
                "source_confidence": raw_payload.get("source_confidence", 1.0),
                "field_provenance": raw_payload.get("field_provenance", {}),
                "unsupported_fields": raw_payload.get("unsupported_fields", []),
                "warnings": raw_payload.get("warnings", []),
            },
        }


def prepare_provider_agnostic_ingestion(envelope: IngestionEnvelopeV1) -> PreparedIngestion:
    """Prepare validated envelope for downstream write paths.

    This step is provider-agnostic by design: it only depends on the envelope
    and canonical contract, not on provider-specific code.
    """
    if not envelope.validation_report.valid or envelope.canonical_activity is None:
        raise ValueError("Envelope is not valid and cannot be ingested")

    canonical_activity = envelope.canonical_activity
    warnings = [issue.message for issue in envelope.validation_report.warnings]
    return PreparedIngestion(
        provider=envelope.provider,
        source_identity_key=source_identity_key(canonical_activity),
        payload_fingerprint=activity_payload_fingerprint(canonical_activity),
        idempotency_key=build_external_idempotency_key(canonical_activity),
        canonical_activity=canonical_activity,
        warnings=warnings,
    )
