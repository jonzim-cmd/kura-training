# TM5 Autopilot Runbook

Last updated: 2026-02-12

## Issue Status

- `kura-training-tm5.1`: completed
- `kura-training-tm5.3`: completed
- `kura-training-tm5.2`: completed
- `kura-training-tm5.5`: completed
- `kura-training-tm5.6`: completed
- `kura-training-tm5.7`: pending
- `kura-training-tm5.9`: pending
- `kura-training-tm5.8`: pending (post-launch)

## Last Commit

- `2763e13` (`feat(kura-training-tm5.5): add provider field mapping matrix v1`)

## Tested Commands

- `cd workers && uv run ruff check src/kura_workers/external_activity_contract.py tests/test_external_activity_contract.py` (green)
- `cd workers && uv run pytest -q tests/test_external_activity_contract.py` (green)
- `cd workers && uv run ruff check src/kura_workers/external_identity.py tests/test_external_identity.py` (green)
- `cd workers && uv run pytest -q tests/test_external_identity.py` (green)
- `cd workers && uv run ruff check src/kura_workers/external_adapter.py tests/test_external_adapter.py` (green)
- `cd workers && uv run pytest -q tests/test_external_adapter.py tests/test_external_activity_contract.py tests/test_external_identity.py` (green)
- `cd workers && uv run ruff check src/kura_workers/external_mapping_matrix.py tests/test_external_mapping_matrix.py` (green)
- `cd workers && uv run pytest -q tests/test_external_mapping_matrix.py tests/test_external_activity_contract.py` (green)
- `cd workers && uv run ruff check src/kura_workers/external_import_pipeline.py src/kura_workers/handlers/external_import.py src/kura_workers/event_conventions.py tests/test_external_import_pipeline.py` (green)
- `cd workers && uv run pytest -q tests/test_external_import_pipeline.py tests/test_external_mapping_matrix.py tests/test_external_identity.py tests/test_external_adapter.py tests/test_external_activity_contract.py` (green)
- `cargo test -p kura-api imports:: -- --nocapture` (green; 5 passed, existing dead-code warnings unchanged)

## Open Risks

- Foreign local change in `workers/tests/test_integration.py` is intentionally ignored per user instruction.
- Import API currently has validator-only Rust unit tests; DB-backed endpoint integration tests are still open.

## Next Step

- Start `kura-training-tm5.7` to integrate `external.activity_imported` into core projections + quality signals.
