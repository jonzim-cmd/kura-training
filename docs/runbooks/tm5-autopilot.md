# TM5 Autopilot Runbook

Last updated: 2026-02-12

## Issue Status

- `kura-training-tm5.1`: completed
- `kura-training-tm5.3`: pending
- `kura-training-tm5.2`: pending
- `kura-training-tm5.5`: pending
- `kura-training-tm5.6`: pending
- `kura-training-tm5.7`: pending
- `kura-training-tm5.9`: pending
- `kura-training-tm5.8`: pending (post-launch)

## Last Commit

- pending checkpoint commit for `kura-training-tm5.1`

## Tested Commands

- `cd workers && uv run ruff check src/kura_workers/external_activity_contract.py tests/test_external_activity_contract.py` (green)
- `cd workers && uv run pytest -q tests/test_external_activity_contract.py` (green)

## Open Risks

- Foreign local change in `workers/tests/test_integration.py` is intentionally ignored per user instruction.

## Next Step

- Start `kura-training-tm5.3` with source identity and dedup/idempotency engine.
