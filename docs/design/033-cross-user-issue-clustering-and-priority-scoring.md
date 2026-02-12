# Cross-User Issue Clustering + Priority Scoring V1 (2zc.2)

Date: 2026-02-12  
Owner: Team (JZ + Codex)  
Issue: `kura-training-2zc.2`

## Problem

Learning telemetry already captures reliability and friction signals, but failures still appear as isolated anecdotes unless they are aggregated across users and time windows.

## Decision

Implement a deterministic clustering pipeline on `learning.signal.logged` with:

1. Stable grouping by `cluster_signature` (no hidden semantic merge).
2. Dual period output (`day` + `week`).
3. Explainable priority score:
   - `priority = frequency * severity * impact * reproducibility`
4. Built-in false-positive controls:
   - minimum support
   - minimum unique pseudo-users
   - low-confidence filtering by default

## Storage

New system-level tables:

- `learning_issue_clusters` (current cluster artifacts)
- `learning_issue_cluster_runs` (run telemetry / diagnostics)

Both are cross-user and privacy-safe:

- no raw user IDs are persisted
- only pseudonymized-user counts and representative signal snippets

## Batch Execution

Clustering refresh is integrated into nightly batch execution (`inference.nightly_refit`), so cluster artifacts are refreshed continuously without requiring a separate scheduler daemon.

## Output Contract (per cluster)

- `summary` (human-readable)
- `score` and `score_factors` (`frequency`, `severity`, `impact`, `reproducibility`)
- `aggregates` (`event_count`, `unique_pseudo_users`, first/last seen, signal counts)
- `affected_workflow_phases`
- `representative_examples`
- `false_positive_controls` (thresholds + status)

## Determinism Guarantees

- Input rows sorted by `(timestamp, id)`
- Group keys sorted before aggregation
- Stable ordering for top signals, phases, and representative examples
- Pure scoring function (same input -> same output)

## Non-Goals (V1)

- No automatic backlog ticket creation yet (handled by `2zc.3`).
- No semantic clustering beyond `cluster_signature` (handled later if needed).
- No adaptive threshold tuning (handled by calibration/drift chain).

## Rollback / Disable

- Disable refresh by skipping `refresh_issue_clusters(...)` call in `inference_nightly`.
- Keep migration tables in place; they are append-safe and do not affect write-path correctness.
