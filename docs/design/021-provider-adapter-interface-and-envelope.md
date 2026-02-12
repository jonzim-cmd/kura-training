# Design 021: Provider Adapter Interface + Ingestion Envelope v1

Status: implemented (2026-02-12)

## Goal

Define one stable adapter interface so Garmin/Strava/TrainingPeaks can plug into
the same ingestion core without duplicating business logic.

## Adapter Interface

Implemented in `workers/src/kura_workers/external_adapter.py`:

- Protocol: `ExternalProviderAdapter`
- Base class: `BaseExternalProviderAdapter`
- Test adapter: `DummyExternalAdapter`

Adapter input:

- `provider_user_id`
- `raw_payload`
- `raw_payload_ref` (optional)
- `ingestion_method` (`file_import|connector_api|manual_backfill`)

Adapter output:

- `IngestionEnvelopeV1`

## Envelope Format v1

Envelope ID: `external_ingestion_envelope.v1`

Fields:

- `envelope_version`
- `provider`
- `raw_payload_ref` (optional)
- `raw_payload_hash`
- `canonical_draft`
- `canonical_activity` (validated model or `null` on validation failure)
- `mapping_metadata`
- `validation_report`
- `ingested_at`

`mapping_metadata` contains:

- `mapping_version`
- `mapped_fields`
- `dropped_fields`
- `unit_conversions`
- `notes`

`validation_report` contains:

- `valid` (boolean)
- `errors[]` (`code`, `field`, `message`, `docs_hint`)
- `warnings[]`

## Provider-Agnostic Core Ingestion

`prepare_provider_agnostic_ingestion(envelope)` consumes only the envelope and
canonical contract and returns deterministic write inputs:

- `source_identity_key`
- `payload_fingerprint`
- `idempotency_key`

This guarantees shared ingestion logic independent of provider-specific mapping.

## Test Coverage

`workers/tests/test_external_adapter.py` covers:

1. Valid envelopes for multiple providers via the same interface.
2. Validation report behavior for invalid canonical drafts.
3. Provider-agnostic ingestion preparation.
4. Rejection behavior for invalid envelopes.
