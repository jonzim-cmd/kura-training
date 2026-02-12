# TM5 Autopilot Runbook

Last updated: 2026-02-12

## Issue Status

- `kura-training-tm5.1`: completed
- `kura-training-tm5.3`: completed
- `kura-training-tm5.2`: completed
- `kura-training-tm5.5`: pending
- `kura-training-tm5.6`: pending
- `kura-training-tm5.7`: pending
- `kura-training-tm5.9`: pending
- `kura-training-tm5.8`: pending (post-launch)

## Last Commit

- `de6f90d` (`feat(kura-training-tm5.3): implement source identity and dedup policy`)

## Tested Commands

- `cd workers && uv run ruff check src/kura_workers/external_activity_contract.py tests/test_external_activity_contract.py` (green)
- `cd workers && uv run pytest -q tests/test_external_activity_contract.py` (green)
- `cd workers && uv run ruff check src/kura_workers/external_identity.py tests/test_external_identity.py` (green)
- `cd workers && uv run pytest -q tests/test_external_identity.py` (green)
- `cd workers && uv run ruff check src/kura_workers/external_adapter.py tests/test_external_adapter.py` (green)
- `cd workers && uv run pytest -q tests/test_external_adapter.py tests/test_external_activity_contract.py tests/test_external_identity.py` (green)

## Open Risks

- Foreign local change in `workers/tests/test_integration.py` is intentionally ignored per user instruction.

## Next Step

- Start `kura-training-tm5.5` with Garmin/Strava/TrainingPeaks mapping matrix and mapping tests.
