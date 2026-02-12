# Design 023: Import Pipeline v1 (FIT/TCX/GPX)

Status: implemented (2026-02-12)

## Goal

Provide a launch-ready file import path that uses the same canonical core used
for future connectors.

Pipeline:

1. queue async import job
2. parse FIT/TCX/GPX payload
3. map provider payload via mapping matrix v1
4. validate `external_activity.v1`
5. apply dedup policy / idempotency
6. write canonical event (`external.activity_imported`) when required
7. persist structured receipt

## Async Job Model

`external_import_jobs` table stores:

- request inputs (`provider`, `provider_user_id`, `file_format`, payload)
- status (`queued|processing|completed|failed`)
- deterministic identity fields (`source_identity_key`, `payload_fingerprint`, `idempotency_key`)
- `receipt` JSON
- classified errors (`error_code`, `error_message`)

API endpoints:

- `POST /v1/imports/jobs` queues a background job (`external_import.process`)
- `GET /v1/imports/jobs/{id}` returns status + receipt

## Error Classification

Pipeline errors are classified by code:

- `parse_error`
- `unsupported_format`
- `mapping_error`
- `validation_error`
- dedup rejection outcomes (`stale_version`, `version_conflict`, `partial_overlap`)

Each failed job stores the code + message in table columns and receipt payload.

## Idempotent Re-Import

Idempotency is guaranteed with deterministic key generation from source identity
plus version/fingerprint:

- same import replay -> dedup skip or idempotent replay receipt
- newer version -> update path
- stale/conflicting versions -> failed with explicit reason

## Receipts

Completed receipt includes:

- format/provider
- dedup decision + outcome + reason
- mapping version
- unsupported fields + warnings
- write result (`created`, `duplicate_skipped`, `idempotent_replay`)
- event id + idempotency key when write happened
