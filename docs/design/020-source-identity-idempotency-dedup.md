# Design 020: Source Identity, Idempotency, and Dedup Policy

Status: implemented (2026-02-12)

## Goal

Avoid duplicate or contradictory external imports by enforcing source-aware
identity and replay-safe idempotent write keys.

## Source Identity

Canonical source identity tuple:

- `provider`
- `provider_user_id`
- `external_activity_id`

This tuple defines one logical external activity independently of transport
(file import vs. connector sync).

Identity key:

- `external-activity-<stable_hash(provider|provider_user_id|external_activity_id)>`

## Idempotency Key Strategy

Write idempotency key is deterministic:

- `external-import-<identity_hash>-<version_hash>`

Version anchor selection:

- If `external_event_version` exists: use that version.
- Else: use payload fingerprint.

Payload fingerprint excludes non-deterministic import timestamps
(`source.imported_at`, `provenance.mapped_at`) to keep retries stable.

## Duplicate Policy Outcomes

Policy function returns one decision + one outcome:

- `create/new_activity`
- `skip/exact_duplicate`
- `update/version_update`
- `reject/stale_version`
- `reject/version_conflict`
- `reject/partial_overlap`

Rules:

1. No existing records -> `create/new_activity`
2. Same version + same fingerprint -> `skip/exact_duplicate`
3. Same version + different fingerprint -> `reject/version_conflict`
4. Newer version than latest known -> `update/version_update`
5. Older version than latest known -> `reject/stale_version`
6. No version + changed fingerprint -> `reject/partial_overlap`

## Update-vs-New Definition

- **New activity**: first record for source identity tuple.
- **Update**: same source identity, strictly newer external version.
- **Not update/new**: same identity with no version and changed payload
  (partial overlap) is rejected to avoid silent corruption.
