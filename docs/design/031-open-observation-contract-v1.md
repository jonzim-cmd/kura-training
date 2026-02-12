# Open Observation Contract V1 (PDC.21)

Date: 2026-02-12
Owner: Team (JZ + Codex)

## Problem
Relevant session insights were often captured only as opaque free text and were
not queryable as structured projections. We needed an open-world contract that:
- preserves context/provenance,
- avoids event-type explosion,
- supports strict typing for high-value known dimensions,
- keeps unknown dimensions storable with explicit quality markers.

## Decision
Introduce `observation.logged` + projection type `open_observations` with:
- one projection key per `dimension`,
- tiered validation (`known`, `provisional`, `unknown`),
- quality flags for normalization/contract gaps,
- no custom schema work per new dimension key.

## Event Contract
`observation.logged` fields:
- `dimension` (required)
- `value` (required)
- `unit` (optional)
- `scale` (optional)
- `context_text` (optional but strongly recommended)
- `tags` (optional)
- `confidence` (optional, normalized/clamped to `0..1`, defaults `0.5`)
- `provenance` (recommended; quality flags added when missing)
- `scope.level` (`session|exercise|set`, default `session`)

## Validation Tiers
- `known`: typed normalization from registry
  - `motivation_pre` (number, expected scale `1..5`)
  - `discomfort_signal` (number `0..10` or boolean)
  - `jump_baseline` (number, normalized to `cm`)
- `provisional`: dimension prefixes `x_`, `custom.`, `provisional.`
- `unknown`: safely stored, no rejection, flagged for quality/mining loop

## Projection Shape
`open_observations/<dimension>` includes:
- `entries[]`: normalized event-derived records with `context_text`, `provenance`,
  `scope`, and `quality_flags`
- `summary`: latest value/unit/confidence/context/timestamp + quality flag counts
- `registry_version`: `open_observation.v1`

This enables direct querying by dimension without adding new projection schema
code for each new observed signal.

## Reliability Semantics
- Saved: persisted `observation.logged` event and projection entry exists.
- Inferred: provenance/source type may indicate inferred extraction; confidence
  remains explicit.
- Unresolved: represented by quality flags (for example unknown dimension,
  missing provenance, invalid confidence coercions).

## Rollback/Disable
- Stop emitting `observation.logged` at the writer.
- Existing data remains isolated in `open_observations` projections.
- Handler can be disabled by removing `open_observations` import registration.
