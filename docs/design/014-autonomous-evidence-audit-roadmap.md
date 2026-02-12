# Autonomous Evidence + Audit Roadmap (Partner Execution Plan)

Date: 2026-02-12
Owner: Team (JZ + Codex)

## Goal
Implement an end-to-end reliability upgrade for open-world training transcripts:
1) raw utterance -> claim -> event lineage,
2) session-level coherence audit,
3) extensible open observation contract,
4) transparent agent reliability UX,
5) calibrated learning loop for extraction quality.

## Scope Boundary
This roadmap covers these issue chains:
- Security prerequisite: `kura-training-ct3.1` -> `kura-training-ct3.6`
- Domain reliability: `kura-training-pdc.19` -> `kura-training-pdc.20` -> `kura-training-pdc.21` -> `kura-training-m3k.1`
- Learning loop: `kura-training-2zc.2` -> `kura-training-2zc.5` -> `kura-training-2zc.3` -> `kura-training-2zc.6` -> `kura-training-2zc.4`

## Exact Execution Order
1. `kura-training-ct3.1` Threat model and security tiering.
2. `kura-training-ct3.6` Server-side invariant enforcement review.
3. `kura-training-pdc.19` Evidence layer V1 (utterance->claim->event lineage).
4. `kura-training-pdc.20` Session audit V2 (cross-event coherence, scale guards).
5. `kura-training-pdc.21` Open observation contract V1 (motivation_pre, discomfort_signal, jump_baseline).
6. `kura-training-m3k.1` Reliability UX protocol (Saved vs Inferred vs Unresolved).
7. `kura-training-2zc.2` Cross-user clustering and priority scoring (finish/extend if partial).
8. `kura-training-2zc.5` Extraction calibration + drift monitoring.
9. `kura-training-2zc.3` Learning-to-backlog bridge and promotion policy.
10. `kura-training-2zc.6` Unknown-dimension mining and proposal loop.
11. `kura-training-2zc.4` Shadow evaluation gate for rollout decisions.

## Per-Issue Working Contract
For each issue in order:
1. `scripts/bd-safe.sh show <id>` and restate root problem in notes.
2. `scripts/bd-safe.sh update <id> --status in_progress`.
3. Implement code/docs/tests end-to-end (do not stop at analysis).
4. Run quality gates relevant to changed components.
5. Add/update tests for regressions introduced by the issue.
6. `scripts/bd-safe.sh close <id>` only after gates pass.
7. `scripts/bd-safe.sh sync` after each closed issue.
8. Commit and push continuously (no local-only completion).

## Mandatory Quality Gates
Always load env first:
`set -a && source .env && set +a`

Run all applicable gates after code changes:
- `ruff check workers/src/ workers/tests/`
- `PYTHONPATH=workers/src uv run --project workers python -m pytest workers/tests/ -q --ignore=workers/tests/test_integration.py`
- `PYTHONPATH=workers/src uv run --project workers python -m pytest workers/tests/test_integration.py -q`
- `cargo test --workspace`

## Compact-Safe Handoff Format
After each issue, write this in the issue notes:
- Implemented artifacts (files, migrations, endpoints, handlers).
- Tests added/updated and exact commands run.
- Risks left, explicit non-goals, and follow-up ticket IDs.
- Rollback/disable path if behavior regresses.

## Copy/Paste Autonomous Prompt
Use this prompt in a fresh full-access Codex session.

```text
You are Codex with full filesystem + network access in /Users/jz/Projekte/Life/kura-training.
Treat JZ and Codex as equal product-engineering partners with shared ownership.
Implement the full roadmap in this exact order:
1) kura-training-ct3.1
2) kura-training-ct3.6
3) kura-training-pdc.19
4) kura-training-pdc.20
5) kura-training-pdc.21
6) kura-training-m3k.1
7) kura-training-2zc.2
8) kura-training-2zc.5
9) kura-training-2zc.3
10) kura-training-2zc.6
11) kura-training-2zc.4

Execution rules:
- Before any command: set -a && source .env && set +a
- Always use scripts/bd-safe.sh for beads operations.
- For each issue: show -> set in_progress -> implement -> test -> close -> bd sync -> commit -> push.
- Never leave work unpushed. If push fails, resolve and retry until successful.
- Never fake completion. If blocked, create a new bd issue with clear dependency and continue with the next unblocked item.
- Keep changes small and integrated; prefer deterministic behavior over heuristic magic.
- For free-text extraction/audit features, persist provenance and confidence explicitly.
- Enforce Saved vs Inferred vs Unresolved user-facing reliability semantics where applicable.

Quality gates after every code-changing issue:
- ruff check workers/src/ workers/tests/
- PYTHONPATH=workers/src uv run --project workers python -m pytest workers/tests/ -q --ignore=workers/tests/test_integration.py
- PYTHONPATH=workers/src uv run --project workers python -m pytest workers/tests/test_integration.py -q
- cargo test --workspace

Session close is mandatory and must include:
- git pull --rebase
- scripts/bd-safe.sh sync
- git push
- git status (must show up-to-date with origin)

Compact-safe documentation requirement per issue notes:
- implemented artifacts
- tests + commands run
- residual risks/non-goals
- follow-up issue IDs
- rollback/disable path

Start now with kura-training-ct3.1 and continue autonomously until the full chain is complete.
```
