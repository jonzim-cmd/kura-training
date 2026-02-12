# Design 030: Session Audit V2 (Cross-Event Coherence + Scale Guards)

Status: implemented (2026-02-12)

## Goal

Extend session audit beyond mention-bound set fields to include session-level
feedback coherence for `session.completed`.

## New Guard Families

### 1) Scale Guards

- `enjoyment` and `perceived_quality` are canonical `1..5`.
- Deterministic transform allowed: when value is in `6..10`, normalize to `value / 2`.
- Out-of-range values (`<1` or `> max`) are unresolved and require clarification.

### 2) Narrative vs Structured Contradiction

Detect contradictions between feedback text (`context/context_text/summary/comment/notes/feeling`)
and structured values:

- high enjoyment/quality with negative narrative hints
- low enjoyment/quality with positive narrative hints
- high exertion with “easy/light” narrative hints
- low exertion with “hard/brutal” narrative hints

Contradictions are unresolved (clarification required).

### 3) Unsupported Inferred Values

If `<field>_source == "inferred"` and no `<field>_evidence_claim_id` exists,
the value is treated as unsupported inference and flagged unresolved.

## Repair vs Clarification Split

- Auto-repair (deterministic/high confidence only):
  - emit `event.retracted` for original `session.completed`
  - emit normalized replacement `session.completed` with `repair_provenance`
- Uncertain cases:
  - no auto-mutation
  - clarification prompt via session audit summary

## Machine-Readable Audit Surface

`write-with-proof` now exposes `session_audit.mismatch_classes` and telemetry
events include the same class set.

Current mismatch classes:

- `missing_mention_bound_field`
- `scale_normalized_to_five`
- `scale_out_of_bounds`
- `narrative_structured_contradiction`
- `unsupported_inferred_value`

## Test Coverage

Added/updated tests cover:

- clean path (no mismatches)
- repaired path (deterministic scale normalization)
- needs-clarification path (narrative contradiction)
- telemetry class propagation and repair event contract
