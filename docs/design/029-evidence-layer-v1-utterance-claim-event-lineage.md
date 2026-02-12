# Design 029: Evidence Layer V1 (Utterance -> Claim -> Event)

Status: implemented (2026-02-12)

## Goal

Make mention-derived writes reconstructable by persisting deterministic claims
with provenance and lineage to the concrete persisted event.

## Storage Path

Evidence is stored as additive sidecar events (no event-store replacement):

- event type: `evidence.claim.logged`
- producer: `/v1/agent/write-with-proof` after durable write receipts
- lineage key: `data.lineage.event_id` (target persisted event)

## Claim Schema V1

Each claim event contains:

- `claim_id` (deterministic hash-based identifier)
- `claim_type` (e.g. `set_context.rest_seconds`)
- `value`
- `unit` (optional)
- `scope` (`level`, `event_type`, optional `session_id`/`exercise_id`)
- `confidence` (numeric `0..1`)
- `provenance`
  - `source_field` (`notes|context_text|utterance`)
  - `source_text` (raw snippet)
  - `source_text_span` (`start`, `end`, `text`)
  - `parser_version` (`mention_parser.v1`)
- `lineage`
  - `event_id`
  - `event_type`
  - `lineage_type` (`supports`)

## Deterministic Extraction Contract

Parser scope in V1:

- `rest_seconds`
- `rir`
- `tempo`
- `set_type`

Claim IDs and idempotency keys are deterministic from:

- user ID
- target event ID
- claim type + canonical value
- source field + source span
- parser version

This enables deterministic replay over identical transcript snippets.

## Query Surface (Audit Explain)

New explain endpoint:

- `GET /v1/agent/evidence/event/{event_id}`

Returns all `evidence.claim.logged` claims linked to `event_id`, including raw
text span provenance and parser metadata.

## Safety and Compatibility

- Existing write-with-proof response contract remains intact.
- Evidence writes are additive and best-effort sidecar writes.
- No mutation of existing event payloads required.
