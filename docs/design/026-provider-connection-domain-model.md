# Design 026: ProviderConnection Domain Model (Post-Launch Ready)

Status: implemented (2026-02-12)

## Goal

Define a post-launch-ready provider connection aggregate for OAuth account
linking, token lifecycle management, consent/scopes, revocation, and audit.

## Domain Aggregate

Storage: `provider_connections` table

Identity:

- `id`
- `user_id`
- `provider` (`garmin|strava|trainingpeaks`)
- `provider_account_id`

Auth state:

- `auth_state` (`linked|refresh_required|revoked|error`)
- `scopes[]`
- `consented_at`

Token metadata:

- `token_expires_at`
- `token_rotated_at`
- `access_token_ref` (stored reference, not exposed in API responses)
- `refresh_token_ref` (stored reference, not exposed in API responses)
- `token_fingerprint` (audit-safe token marker)

Sync metadata:

- `sync_cursor`
- `last_sync_at`

Revocation:

- `revoked_at`
- `revoked_reason`
- `revoked_by`

Security/audit:

- `last_oauth_state_nonce`
- `last_error_code`
- `last_error_at`
- `created_at`, `updated_at`
- `created_by`, `updated_by`

## API Contract

Routes:

- `GET /v1/providers/connections`
- `POST /v1/providers/connections` (upsert metadata)
- `POST /v1/providers/connections/{id}/revoke`

Contract behaviors:

- Upsert supports `linked|refresh_required|error`; revoked is explicit via revoke endpoint.
- Revoke clears live token refs and writes revocation audit fields.
- Responses include `adapter_context`:
  - `provider_user_id`
  - `ingestion_method=connector_api`
  - `ready` flag for adapter pipeline use

## Integration with Adapter Pipeline

`adapter_context` and stable provider/user identity fields make connection records
directly consumable by the adapter ingestion flow:

- provider + provider_account_id map to canonical source identity
- sync cursor and token metadata enable connector scheduling and incremental sync
- revocation state cleanly gates connector activation
