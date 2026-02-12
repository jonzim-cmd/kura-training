# Design 028: Server-Side Invariant Enforcement Review (ct3.6)

Status: implemented (2026-02-12)

## Goal

Harden critical write-path invariants so agent prompting/tool misuse cannot
bypass core domain safety rules.

## Critical Invariant Inventory (Prioritized)

| Priority | Invariant ID | Threat Class | Event Scope | Server Check | Error Code(s) |
|---|---|---|---|---|---|
| P0 | `INV-SRV-001` | Scope tampering / destructive misuse | `event.retracted` | `data.retracted_event_id` must exist and be UUID | `inv_retraction_target_required`, `inv_retraction_target_invalid_uuid` |
| P0 | `INV-SRV-002` | Silent data corruption via malformed patch | `set.corrected` | `data.target_event_id` UUID + non-empty `data.changed_fields` object | `inv_set_correction_target_required`, `inv_set_correction_target_invalid_uuid`, `inv_set_correction_changed_fields_required`, `inv_set_correction_changed_fields_invalid`, `inv_set_correction_changed_fields_empty`, `inv_set_correction_changed_fields_key_invalid` |
| P1 | `INV-SRV-003` | Unbounded custom-rule abuse / invalid rule payloads | `projection_rule.created` | Require valid `name`, `rule_type`, non-empty `source_events`, `fields`, categorized `group_by` in fields, bounded list sizes | `inv_projection_rule_*` family |
| P1 | `INV-SRV-004` | Ambiguous archive intent / wrong target | `projection_rule.archived` | Require non-empty `data.name` | `inv_projection_rule_archive_name_required` |

## Gap Analysis -> Closure

Previous gaps:

- Event endpoint only enforced generic shape (`event_type`, `idempotency_key`).
- Critical correction/retraction/rule payload invariants were not enforced at
  ingress; malformed events could enter the immutable stream and create
  downstream ambiguity.
- Violations returned generic `validation_failed`, not invariant-specific codes.

Closure in this issue:

- Added event-type-specific invariant enforcement in
  `api/src/routes/events.rs` (`validate_critical_invariants` and scoped helpers).
- Added dedicated `AppError::PolicyViolation` (HTTP 422) with explicit
  machine-readable `error` codes per invariant failure in
  `api/src/error.rs`.
- Added regression tests for bypass scenarios in
  `api/src/routes/events.rs` test module.

## Enforcement Contract

Violation responses now distinguish:

- generic input issues -> `validation_failed` (400)
- critical invariant/policy issues -> specific `inv_*` code (422)

This enables deterministic client/agent handling and telemetry aggregation per
invariant failure family.

## Non-Goals

- No runtime profile-switch automation (covered by ct3.7 track).
- No cross-event historical authorization model changes in this issue.
