# Design 016: Repair Provenance Contract

Status: implemented (2026-02-11)

## Goal

Every repair must carry machine-readable provenance so downstream policy and UI
layers can distinguish explicit, inferred, and estimated corrections.

## Canonical Provenance Object

```json
{
  "source_type": "explicit|inferred|estimated|user_confirmed",
  "confidence": 0.0,
  "confidence_band": "low|medium|high",
  "applies_scope": "single_set|exercise_session|session",
  "reason": "string"
}
```

## Current Integration

- Repair proposals in `quality_health` include:
  - per-entry provenance (`repair_provenance.entries`)
  - aggregated provenance (`repair_provenance.summary`)
- Repair events (`quality.fix.applied`, `quality.fix.rejected`) include
  `repair_provenance_summary` for audit and telemetry.
- Proposal filters expose confidence buckets:
  - `repair_confidence_filters.high_confidence_ids`
  - `repair_confidence_filters.low_confidence_ids`
- Auto-apply gate rejects low-confidence proposals (`low_confidence_repair`).

## Backward Compatibility

- Provenance fields are additive and optional in event payloads.
- Existing consumers that ignore unknown fields continue to work unchanged.

